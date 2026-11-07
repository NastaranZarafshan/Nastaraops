"""Single-consumer Redis work queue. PostgreSQL tasks are authoritative."""

import asyncio
import logging
import time

import redis.asyncio as redis
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy import select

from app.audit import append_event
from app.config import get_settings
from app.db import SessionLocal, create_schema, engine
from app.evaluation import EVALUATOR_VERSION, evaluate_structural
from app.graph import build_graph
from app.models import EvaluationRun, MemoryEntry, Task, utcnow

log = logging.getLogger("agent_worker")
QUEUE = "agentops:queue"
PROCESSING = "agentops:processing"


async def recover_queued(r: redis.Redis) -> None:
    # This demonstrator intentionally uses one worker. Re-enqueue all unfinished DB jobs
    # on restart; duplicate queue messages are ignored once terminal.
    async with SessionLocal() as session:
        rows = (await session.scalars(select(Task).where(Task.status.in_(
            ["queued", "running", "resume_queued"])))).all()
        for task in rows:
            if task.status == "running":
                task.status = "queued"
                await append_event(session, task_id=task.id, actor="system", action="recovered",
                                   detail={"reason": "worker restart"})
        await session.commit()
    await r.delete(PROCESSING)
    for task in rows:
        await r.lpush(QUEUE, task.id)
    log.info("Recovered %d nonterminal jobs", len(rows))


async def persist_state(task_id: str, state: dict, paused: bool, elapsed_ms: float) -> None:
    async with SessionLocal() as session:
        task = await session.get(Task, task_id, with_for_update=True)
        if task is None:
            return
        task.agent = state.get("agent", task.agent)
        task.draft_json = state.get("draft", task.draft_json)
        task.risk_reasons = state.get("risk_reasons", task.risk_reasons or [])
        task.trace_json = state.get("execution_trace", task.trace_json or [])
        if paused:
            task.status = "awaiting_approval"
            task.duration_ms = elapsed_ms
            await append_event(session, task_id=task_id, actor="langgraph", action="approval_requested",
                               detail={"risk_reasons": task.risk_reasons})
        else:
            task.status = state["final_status"]
            task.result_json = state["final_output"]
            task.finished_at = utcnow()
            task.duration_ms = elapsed_ms
            await append_event(session, task_id=task_id, actor="langgraph", action=task.status,
                               detail={"agent": task.agent, "approval_required": bool(task.risk_reasons)})
            if task.status == "completed":
                session.add(MemoryEntry(task_id=task_id, kind=task.kind,
                                        summary=str(task.draft_json.get("summary", ""))[:1200]))
                # Cheap and deterministic evaluation is automatic; never block on an LLM judge.
                try:
                    evaluation = evaluate_structural(content=task.content, draft=task.draft_json or {})
                    session.add(EvaluationRun(task_id=task_id, mode="automatic",
                                              status="completed", evaluator_version=EVALUATOR_VERSION,
                                              metrics_json=evaluation["metrics"],
                                              details_json=evaluation["details"]))
                except Exception:
                    log.exception("Structural evaluation skipped for task %s", task_id)
        await session.commit()


async def process_one(task_id: str, graph) -> None:
    async with SessionLocal() as session:
        task = await session.get(Task, task_id, with_for_update=True)
        if task is None or task.status not in {"queued", "resume_queued"}:
            return
        resume = task.status == "resume_queued"
        if resume and not task.decision_json:
            raise ValueError("Resume queued without a recorded review decision")
        decision = task.decision_json
        input_state = {"task_id": task.id, "kind": task.kind, "title": task.title,
                       "content": task.content, "metadata": task.metadata_json or {}}
        task.status = "running"
        task.started_at = task.started_at or utcnow()
        task.error = None
        await append_event(session, task_id=task.id, actor="worker", action="started",
                           detail={"resume": resume})
        await session.commit()

    config = {"configurable": {"thread_id": task_id}}
    started = time.monotonic()
    # A crash may occur after the final LangGraph checkpoint but before SQL projection.
    # In that case, project the completed state rather than replaying a terminal graph.
    snapshot = await graph.aget_state(config)
    if snapshot.values and not snapshot.next and snapshot.values.get("final_status"):
        state = snapshot.values
        paused = False
    else:
        if resume:
            if not snapshot.next or "human_review" not in snapshot.next:
                raise RuntimeError("No matching paused approval checkpoint")
            result = await graph.ainvoke(Command(resume=decision), config)
        else:
            result = await graph.ainvoke(input_state, config)
        snapshot = await graph.aget_state(config)
        state = dict(snapshot.values or {})
        paused = bool(snapshot.next) or "__interrupt__" in result
        if paused and "human_review" not in snapshot.next:
            raise RuntimeError("Unexpected LangGraph pause; cannot request an approval")
        if not paused and not state.get("final_status"):
            raise RuntimeError("LangGraph stopped without a final output")
    await persist_state(task_id, state, paused, round((time.monotonic() - started) * 1000, 1))


async def worker_main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    await create_schema()
    r = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with AsyncPostgresSaver.from_conn_string(settings.checkpoint_dsn) as saver:
            await saver.setup()
            graph = build_graph(saver)
            await recover_queued(r)
            while True:
                task_id = await r.brpoplpush(QUEUE, PROCESSING, timeout=5)
                if not task_id:
                    continue
                try:
                    await process_one(task_id, graph)
                except Exception as exc:
                    log.exception("Task %s failed", task_id)
                    async with SessionLocal() as session:
                        task = await session.get(Task, task_id, with_for_update=True)
                        if task and task.status == "running":
                            task.status = "failed"
                            task.error = f"{type(exc).__name__}: {str(exc)[:300]}"
                            task.finished_at = utcnow()
                            await append_event(session, task_id=task_id, actor="worker", action="failed",
                                               detail={"error_type": type(exc).__name__})
                            await session.commit()
                finally:
                    await r.lrem(PROCESSING, 1, task_id)
    finally:
        await r.aclose()
        await engine.dispose()


if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy()
        )

    asyncio.run(worker_main())

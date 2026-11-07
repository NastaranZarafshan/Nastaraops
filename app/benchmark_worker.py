"""Background benchmark executor; called by the existing Redis worker process."""
from __future__ import annotations

import logging
import time

import httpx
import redis.asyncio as redis
from sqlalchemy import select

from app.audit import append_event
from app.benchmark import (dataset_snapshot, judge_case, summarise_predictions,
                           BASELINE_RUNNER_VERSION, CALIBRATED_RUNNER_VERSION,
                           LEGACY_CALIBRATED_RUNNER_VERSION, PREVIOUS_CALIBRATED_RUNNER_VERSION)
from app.config import get_settings
from app.db import SessionLocal
from app.models import BenchmarkRun, ReliabilityStudy, utcnow

log = logging.getLogger("agent_benchmark")
BENCHMARK_QUEUE = "agentops:benchmark_queue"


async def recover_benchmarks(r: redis.Redis) -> None:
    """Restore unfinished benchmark runs on worker restart without erasing predictions."""
    async with SessionLocal() as session:
        runs = (await session.scalars(select(BenchmarkRun).where(
            BenchmarkRun.status.in_(["queued", "running"])))).all()
        for run in runs:
            run.status = "queued"
        await session.commit()
    await r.delete(BENCHMARK_QUEUE)
    for run in runs:
        await r.lpush(BENCHMARK_QUEUE, run.id)
    log.info("Recovered %s benchmark runs", len(runs))


async def process_benchmark(run_id: str) -> None:
    async with SessionLocal() as session:
        run = await session.get(BenchmarkRun, run_id, with_for_update=True)
        if run is None or run.status not in {"queued", "running"}:
            return
        from app.reliability import load_dataset
        try:
            dataset, dataset_hash = load_dataset(run.dataset_id)
        except (ValueError, FileNotFoundError) as exc:
            run.status = "failed"
            run.error = f"Pinned dataset unavailable: {str(exc)[:150]}"
            run.finished_at = utcnow()
            await session.commit()
            return
        if run.dataset_sha256 != dataset_hash:
            run.status = "failed"
            run.error = "Dataset checksum changed; run cannot resume against different labels."
            run.finished_at = utcnow()
            await session.commit()
            return
        if run.dataset_id == "agentops-holdout-v7-100":
            from app.reliability import freeze_hash
            studies = (await session.scalars(select(ReliabilityStudy))).all()
            matched = next((s for s in studies if run_id in (s.run_ids_json or [])),None)
            if matched is None or matched.judge_source_sha256 != freeze_hash():
                run.status = "failed"
                run.error = "Frozen V6.2 judge source hash changed or study metadata missing; refusing to score"
                run.finished_at = utcnow()
                await session.commit()
                return
        run.status = "running"
        run.started_at = run.started_at or utcnow()
        await session.commit()
    cases = dataset["cases"]
    # Keep historical unfinished V5 runs using their original, unchanged judge.
    profile = ({BASELINE_RUNNER_VERSION: "baseline",
                CALIBRATED_RUNNER_VERSION: "calibrated",
                LEGACY_CALIBRATED_RUNNER_VERSION: "calibrated-v6-legacy",
                PREVIOUS_CALIBRATED_RUNNER_VERSION: "calibrated-v6-1"}).get(run.runner_version)
    if profile is None:
        async with SessionLocal() as session:
            stale = await session.get(BenchmarkRun, run_id, with_for_update=True)
            stale.status = "failed"
            stale.error = f"Unknown pinned benchmark runner version: {stale.runner_version}"
            stale.finished_at = utcnow()
            await session.commit()
        return
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as client:
        for case in cases:
            async with SessionLocal() as session:
                run = await session.get(BenchmarkRun, run_id)
                if run is None or run.status != "running":
                    return
                prior = {item["case_id"]: item for item in (run.predictions_json or [])}
            if case["case_id"] in prior:
                continue
            start = time.monotonic()
            record = {"case_id": case["case_id"], "gold_verdict": case["gold_verdict"],
                      "category": case["category"], "source_trust": case["sources"][0]["trust_level"]}
            try:
                judged = await judge_case(case, client=client, profile=profile)
                record.update(judged)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                record["error"] = f"{type(exc).__name__}: Ollama not reachable"
                # Connection outage: fail early rather than inventing 40 scores.
                async with SessionLocal() as session:
                    run = await session.get(BenchmarkRun, run_id, with_for_update=True)
                    records = list(run.predictions_json or []) + [record]
                    run.predictions_json = records
                    run.progress = len(records)
                    run.metrics_json = summarise_predictions(cases, records)
                    run.status = "failed"
                    run.error = "Ollama connection/timeout failure; inspect worker and Ollama logs."
                    run.finished_at = utcnow()
                    await append_event(session, task_id=None, actor="benchmark", action="benchmark_failed",
                                       detail={"benchmark_id": run.id, "failure_type": type(exc).__name__})
                    await session.commit()
                return
            except (Exception,) as exc:
                # A malformed output is an error, not a model-quality verdict.
                record["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                log.warning("Benchmark case %s unscored: %s", case["case_id"], type(exc).__name__)
            record["latency_ms"] = round((time.monotonic() - start) * 1000)
            async with SessionLocal() as session:
                run = await session.get(BenchmarkRun, run_id, with_for_update=True)
                records = list(run.predictions_json or []) + [record]
                run.predictions_json = records
                run.progress = len(records)
                run.metrics_json = summarise_predictions(cases, records)
                await session.commit()
    async with SessionLocal() as session:
        run = await session.get(BenchmarkRun, run_id, with_for_update=True)
        run.status = "completed_errors" if run.metrics_json.get("error_count", 0) else "completed"
        run.finished_at = utcnow()
        if run.status == "completed_errors":
            run.error = f"{run.metrics_json['error_count']} of {run.total} cases were unscored; inspect per-case errors."
        await append_event(session, task_id=None, actor="benchmark", action="benchmark_finished",
                           detail={"benchmark_id": run.id, "dataset_sha256": dataset_hash,
                                   "count": run.total, "scored": run.metrics_json.get("evaluated_count", 0),
                                   "status": run.status})
        await session.commit()


async def benchmark_main() -> None:
    """Independent Redis consumer; never stalls production workflow processing."""
    import asyncio
    from app.db import create_schema, engine
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    await create_schema()
    r = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await recover_benchmarks(r)
        while True:
            item = await r.blpop(BENCHMARK_QUEUE, timeout=5)
            if not item:
                continue
            run_id = item[1]
            try:
                await process_benchmark(run_id)
            except Exception as exc:
                log.exception("Benchmark %s interrupted", run_id)
                async with SessionLocal() as session:
                    run = await session.get(BenchmarkRun, run_id, with_for_update=True)
                    if run and run.status in {"queued", "running"}:
                        run.status = "failed"
                        run.error = f"{type(exc).__name__}: {str(exc)[:180]}"
                        run.finished_at = utcnow()
                        await append_event(session, task_id=None, actor="benchmark", action="benchmark_failed",
                                           detail={"benchmark_id": run_id, "error_type": type(exc).__name__})
                        await session.commit()
    finally:
        await r.aclose()
        await engine.dispose()


if __name__ == "__main__":
    import asyncio
    asyncio.run(benchmark_main())

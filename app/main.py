"""FastAPI control plane + JSON API + hosted dashboard."""

import hashlib
import io
import uuid
import zipfile
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

import httpx
import redis.asyncio as redis
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pypdf import PdfReader
from sqlalchemy import select, text

from app.audit import append_event, verify_chain
from app.human_review import (V8_REVIEW_VERSION, LABELS as HUMAN_LABELS, review_dataset, current_decisions, resolve_case,
                              review_summary, safe_review_case, safe_review_list,
                              export_csv as export_human_review_csv, human_vs_model)
from app.review_import import (IMPORT_VERSION, MAX_UPLOAD_BYTES, ImportFormatError, parse_upload, prepare_import)
from app.single_review import (SINGLE_REVIEW_VERSION, single_vote_history, single_queue,
                               single_case_payload, single_summary, single_model_comparison, single_export)
from app.benchmark import (dataset_snapshot, manifest, summarise_predictions,
                           RUNNER_VERSION, PROFILES, compare_paired_predictions)
from app.reliability import (HOLDOUT_ID, holdout_manifest, load_dataset, freeze_hash,
                             reliability_report, markdown_report, csv_report, detailed_predictions_csv)
from app.benchmark_worker import BENCHMARK_QUEUE
from app.config import get_settings
from app.db import SessionLocal, create_schema, engine
from app.metrics import collect_metrics
from app.evaluation import EVALUATOR_VERSION, evaluate_structural, evaluate_semantic
from app.models import AuditEvent, EvaluationRun, BenchmarkRun, ReliabilityStudy, HumanReviewDecision, KnowledgeDocument, MemoryEntry, Task
from app.schemas import (AuditView, DecisionCreate, EvaluationCreate, EvaluationView, BenchmarkRunView,
                         KnowledgeDocumentView, KnowledgeSearchRequest, MemoryView, TaskCreate, TaskView)
from app.security import require_api_key
from app.rag import delete_document_points, index_document, search_knowledge
from app.worker import QUEUE

settings = get_settings()
redis_client = redis.from_url(settings.redis_url, decode_responses=True)
ROOT = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_schema()
    yield
    await redis_client.aclose()
    await engine.dispose()


app = FastAPI(title="NastaraOps Enterprise AI Platform", version="1.9.0", lifespan=lifespan,
              description="Auditable multi-agent workflow control plane")


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(ROOT / "index.html")


@app.get("/assets/{name}", include_in_schema=False)
async def asset(name: str):
    if name not in {"styles.css", "app.js", "nastaraops-icon.svg"}:
        raise HTTPException(404, "Asset not found")
    return FileResponse(ROOT / name)


# V9 public research snapshot: read-only, access-controlled, no raw user-review exports.
V9_PUBLIC = Path(__file__).resolve().parents[1] / "reports" / "public"

@app.get("/api/research/v9/summary", dependencies=[Depends(require_api_key)])
async def v9_research_summary():
    from app.research_report import REPORT_VERSION
    import json
    path = V9_PUBLIC / "research_metrics.json"
    if not path.is_file():
        raise HTTPException(503, "V9 research snapshot is not installed")
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("report_version") != REPORT_VERSION:
        raise HTTPException(503, "V9 report version mismatch")
    return result

@app.get("/api/research/v9/report.md", dependencies=[Depends(require_api_key)])
async def v9_research_markdown():
    path = V9_PUBLIC / "RESEARCH_REPORT.md"
    if not path.is_file():
        raise HTTPException(503, "V9 Markdown report is not installed")
    return FileResponse(path, media_type="text/markdown; charset=utf-8",
                        filename="NastaraOps_Research_Report.md")


@app.get("/health")
async def health():
    return {"status": "ok", "app": "nastaraops", "llm_mode": settings.llm_mode}


@app.get("/ready")
async def ready():
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        await redis_client.ping()
        async with httpx.AsyncClient(timeout=3) as client:
            qdrant_health = await client.get(settings.qdrant_url.rstrip("/") + "/healthz")
            qdrant_health.raise_for_status()
            if settings.llm_mode == "ollama":
                ollama_health = await client.get(settings.ollama_base_url.rstrip("/") + "/api/tags")
                ollama_health.raise_for_status()
    except Exception:
        raise HTTPException(503, "A required dependency is unavailable") from None
    return {"status": "ready"}


@app.post("/api/tasks", response_model=TaskView, status_code=202, dependencies=[Depends(require_api_key)])
async def create_task(data: TaskCreate):
    task = Task(id=str(uuid.uuid4()), kind=data.kind, title=data.title,
                content=data.content, metadata_json=data.metadata)
    async with SessionLocal() as session:
        session.add(task)
        await session.flush()
        await append_event(session, task_id=task.id, actor="api", action="created",
                           detail={"kind": task.kind, "title": task.title})
        await session.commit()
        await session.refresh(task)
    # A Redis outage leaves the task queued in PostgreSQL for worker recovery.
    try:
        await redis_client.lpush(QUEUE, task.id)
    except redis.RedisError:
        pass
    return task


@app.post("/api/tasks/upload", response_model=TaskView, status_code=202,
          dependencies=[Depends(require_api_key)])
async def upload_document(file: UploadFile = File(...), title: str = Form(default="Uploaded document"),
                          sensitive: bool = Form(default=False), requested_action: str = Form(default="")):
    pages, safe_name = await _extract_upload(file, allowed={".pdf", ".txt", ".md", ".docx"})
    content = "\n\n".join(text for text, _ in pages)
    if not content.strip():
        raise HTTPException(422, "No searchable text detected; scanned PDFs require OCR")
    data = TaskCreate(kind="document", title=title[:180], content=content[:30000],
                      metadata={"source": "upload", "filename": safe_name[:150],
                                "sensitive": sensitive, "requested_action": requested_action})
    return await create_task(data)


async def _extract_upload(file: UploadFile, *, allowed: set[str]) -> tuple[list[tuple[str, int | None]], str]:
    safe_name = Path(file.filename or "upload").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(415, f"Supported file types: {', '.join(sorted(allowed))}")
    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(413, "Upload exceeds the configured size limit")
    try:
        if suffix == ".pdf":
            reader = PdfReader(io.BytesIO(raw), strict=False)
            if reader.is_encrypted or len(reader.pages) > 100:
                raise ValueError("Encrypted PDFs and files over 100 pages are unsupported")
            pages = [(page.extract_text() or "", index + 1) for index, page in enumerate(reader.pages)]
        elif suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                document_xml = ET.fromstring(archive.read("word/document.xml"))
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paragraphs = []
            for paragraph in document_xml.findall(".//w:p", namespace):
                value = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
                if value.strip():
                    paragraphs.append(value)
            pages = [("\n".join(paragraphs), None)]
        else:
            pages = [(raw.decode("utf-8-sig"), None)]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Cannot extract text from this file") from None
    return pages, safe_name


@app.get("/api/knowledge/documents", response_model=list[KnowledgeDocumentView],
         dependencies=[Depends(require_api_key)])
async def list_knowledge_documents(limit: int = Query(default=100, ge=1, le=200)):
    async with SessionLocal() as session:
        query = select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc()).limit(limit)
        return (await session.scalars(query)).all()


@app.post("/api/knowledge/upload", response_model=KnowledgeDocumentView, status_code=201,
          dependencies=[Depends(require_api_key)])
async def upload_knowledge_document(file: UploadFile = File(...), title: str = Form(default="")):
    if settings.llm_mode != "ollama":
        raise HTTPException(409, "Set LLM_MODE=ollama and start the GPU profile before indexing knowledge documents")
    pages, safe_name = await _extract_upload(file, allowed={".pdf", ".txt", ".md", ".docx"})
    if not any(text.strip() for text, _ in pages):
        raise HTTPException(422, "No searchable text detected; scanned PDFs require OCR")
    raw_hash = hashlib.sha256("\n".join(text for text, _ in pages).encode("utf-8")).hexdigest()
    doc_id = str(uuid.uuid4())
    display_title = (title.strip() or Path(safe_name).stem)[:180]
    doc = KnowledgeDocument(id=doc_id, title=display_title, filename=safe_name[:180],
                            file_type=Path(safe_name).suffix.lower().lstrip("."),
                            sha256=raw_hash, status="processing", chunk_count=0, uploaded_by="api")
    async with SessionLocal() as session:
        duplicate = await session.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.sha256 == raw_hash, KnowledgeDocument.status == "ready"))
        if duplicate:
            raise HTTPException(409, f"This file is already indexed as '{duplicate.title}'")
        session.add(doc)
        await append_event(session, task_id=None, actor="api", action="knowledge_upload_started",
                           detail={"document_id": doc_id, "filename": safe_name, "sha256": raw_hash})
        await session.commit()

    try:
        chunk_count = await index_document(document_id=doc_id, title=display_title,
                                           filename=safe_name, pages=pages)
    except Exception as exc:
        try:
            await delete_document_points(doc_id)
        except Exception:
            pass
        async with SessionLocal() as session:
            failed = await session.get(KnowledgeDocument, doc_id)
            if failed:
                failed.status = "failed"
                failed.error = f"{type(exc).__name__}: {str(exc)[:350]}"
                await append_event(session, task_id=None, actor="rag", action="knowledge_index_failed",
                                   detail={"document_id": doc_id, "error_type": type(exc).__name__})
                await session.commit()
        raise HTTPException(502, _friendly_rag_error(exc)) from None

    async with SessionLocal() as session:
        indexed = await session.get(KnowledgeDocument, doc_id)
        indexed.status = "ready"
        indexed.chunk_count = chunk_count
        indexed.indexed_at = datetime.now(timezone.utc)
        await append_event(session, task_id=None, actor="rag", action="knowledge_indexed",
                           detail={"document_id": doc_id, "chunks": chunk_count,
                                   "embedding_model": settings.ollama_embed_model})
        await session.commit()
        await session.refresh(indexed)
        return indexed


def _friendly_rag_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "404" in message or "not found" in message or "model" in message and "pull" in message:
        return (f"Embedding model '{settings.ollama_embed_model}' is unavailable. Run: "
                f"docker compose exec ollama ollama pull {settings.ollama_embed_model}")
    if "connect" in message or "timeout" in message:
        return "Cannot reach Ollama or Qdrant. Check their Docker services and logs."
    return f"Knowledge indexing failed ({type(exc).__name__}). Check API logs for details."


@app.post("/api/knowledge/search", dependencies=[Depends(require_api_key)])
async def search_knowledge_api(payload: KnowledgeSearchRequest):
    if settings.llm_mode != "ollama":
        raise HTTPException(409, "Semantic search requires LLM_MODE=ollama and the Ollama embedding model")
    try:
        return {"query": payload.query, "results": await search_knowledge(payload.query, limit=payload.limit)}
    except Exception as exc:
        raise HTTPException(502, _friendly_rag_error(exc)) from None


@app.delete("/api/knowledge/documents/{document_id}", status_code=204,
            dependencies=[Depends(require_api_key)])
async def delete_knowledge_document(document_id: str):
    async with SessionLocal() as session:
        doc = await session.get(KnowledgeDocument, document_id)
        if not doc:
            raise HTTPException(404, "Knowledge document not found")
        filename = doc.filename
    try:
        await delete_document_points(document_id)
    except Exception as exc:
        raise HTTPException(502, _friendly_rag_error(exc)) from None
    async with SessionLocal() as session:
        doc = await session.get(KnowledgeDocument, document_id)
        if doc:
            await session.delete(doc)
            await append_event(session, task_id=None, actor="api", action="knowledge_document_deleted",
                               detail={"document_id": document_id, "filename": filename})
            await session.commit()
    return None


@app.get("/api/tasks", response_model=list[TaskView], dependencies=[Depends(require_api_key)])
async def list_tasks(status: str | None = None, kind: str | None = None,
                     limit: int = Query(default=50, ge=1, le=100),
                     offset: int = Query(default=0, ge=0)):
    query = select(Task).order_by(Task.created_at.desc(), Task.id.desc()).limit(limit).offset(offset)
    if status:
        query = query.where(Task.status == status)
    if kind:
        query = query.where(Task.kind == kind)
    async with SessionLocal() as session:
        return (await session.scalars(query)).all()


@app.get("/api/tasks/{task_id}", response_model=TaskView, dependencies=[Depends(require_api_key)])
async def get_task(task_id: str):
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        return task


@app.post("/api/tasks/{task_id}/decision", response_model=TaskView,
          dependencies=[Depends(require_api_key)])
async def decide(task_id: str, payload: DecisionCreate):
    async with SessionLocal() as session:
        task = await session.get(Task, task_id, with_for_update=True)
        if not task:
            raise HTTPException(404, "Task not found")
        if task.status != "awaiting_approval":
            raise HTTPException(409, "Task is not awaiting human approval")
        task.decision_json = payload.model_dump()
        task.status = "resume_queued"
        await append_event(session, task_id=task.id, actor=payload.reviewer,
                           action="approved" if payload.approved else "denied",
                           detail={"note": payload.note})
        await session.commit()
        await session.refresh(task)
    try:
        await redis_client.lpush(QUEUE, task.id)
    except redis.RedisError:
        pass
    return task


@app.get("/api/metrics", dependencies=[Depends(require_api_key)])
async def metrics():
    async with SessionLocal() as session:
        return await collect_metrics(session)


@app.get("/api/memory", response_model=list[MemoryView], dependencies=[Depends(require_api_key)])
async def list_memory(limit: int = Query(default=30, ge=1, le=100)):
    async with SessionLocal() as session:
        return (await session.scalars(select(MemoryEntry)
                .order_by(MemoryEntry.created_at.desc()).limit(limit))).all()


@app.get("/api/audit", response_model=list[AuditView], dependencies=[Depends(require_api_key)])
async def list_audit(task_id: str | None = None, limit: int = Query(default=70, ge=1, le=200)):
    query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if task_id:
        query = query.where(AuditEvent.task_id == task_id)
    async with SessionLocal() as session:
        return (await session.scalars(query)).all()


@app.get("/api/audit/verify", dependencies=[Depends(require_api_key)])
async def audit_verify():
    async with SessionLocal() as session:
        valid, total, bad_id = await verify_chain(session)
    return {"valid": valid, "entries_checked": total, "first_invalid_id": bad_id}


@app.get("/api/evaluations", response_model=list[EvaluationView], dependencies=[Depends(require_api_key)])
async def list_evaluations(task_id: str | None = None, limit: int = Query(default=100, ge=1, le=250)):
    query = select(EvaluationRun).order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc()).limit(limit)
    if task_id:
        query = query.where(EvaluationRun.task_id == task_id)
    async with SessionLocal() as session:
        return (await session.scalars(query)).all()


@app.get("/api/evaluations/summary", dependencies=[Depends(require_api_key)])
async def evaluation_summary():
    """Small recent-window summary, explicit denominator; not a global success rate."""
    async with SessionLocal() as session:
        runs = (await session.scalars(select(EvaluationRun).order_by(
            EvaluationRun.created_at.desc(), EvaluationRun.id.desc()).limit(250))).all()
    latest = {}
    for run in runs:
        if run.task_id not in latest and run.status == "completed":
            latest[run.task_id] = run
    judge_runs = sum(1 for row in runs if row.mode == "llm_judge" and row.status == "completed")
    return {"recent_runs": len(runs), "tasks_evaluated": len(latest),
            "judge_runs": judge_runs, "window_limit": 250,
            "metric_coverage": {
                key: {"evaluated": len(vals), "mean": round(sum(vals) / len(vals), 4) if vals else None}
                for key in ("question_tracking_rate", "citation_id_integrity", "faithfulness_judge",
                            "answer_relevancy_judge", "context_precision_gold", "context_recall_gold")
                for vals in [[float(r.metrics_json[key]) for r in latest.values()
                              if isinstance(r.metrics_json, dict)
                              and isinstance(r.metrics_json.get(key), (int, float))]]
            }}


@app.post("/api/evaluations/tasks/{task_id}", response_model=EvaluationView, status_code=201,
          dependencies=[Depends(require_api_key)])
async def run_evaluation(task_id: str, payload: EvaluationCreate):
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        if task.status not in {"completed", "rejected"} or not task.draft_json:
            raise HTTPException(409, "Evaluate a finalized task with a saved draft")
        content, draft = task.content, task.draft_json
    if payload.use_llm_judge and settings.llm_mode != "ollama":
        raise HTTPException(409, "The LLM judge requires LLM_MODE=ollama")
    result = evaluate_structural(content=content, draft=draft,
                                 gold_source_ids=payload.gold_source_ids)
    status, error = "completed", None
    if payload.use_llm_judge:
        try:
            semantic = await evaluate_semantic(content=content, draft=draft)
            result["metrics"].update(semantic["metrics"])
            result["details"].update(semantic["details"])
        except Exception as exc:
            # Preserve diagnostic scores, but NEVER record unavailable judge metrics as success.
            status = "failed"
            error = f"{type(exc).__name__}: {str(exc)[:260]}"
    run = EvaluationRun(id=str(uuid.uuid4()), task_id=task_id,
                        mode="llm_judge" if payload.use_llm_judge else "manual_structural",
                        evaluator_version=EVALUATOR_VERSION, status=status,
                        metrics_json=result["metrics"], details_json=result["details"],
                        judge_model=settings.ollama_model if payload.use_llm_judge else None, error=error)
    async with SessionLocal() as session:
        session.add(run)
        await append_event(session, task_id=task_id, actor="evaluator", action="evaluation_finished",
                           detail={"evaluation_id": run.id, "mode": run.mode, "status": status})
        await session.commit()
        await session.refresh(run)
    return run


@app.get("/api/benchmarks/manifest", dependencies=[Depends(require_api_key)])
async def get_benchmark_manifest():
    """Static, fully inspectable synthetic reference dataset."""
    return manifest()


@app.get("/api/benchmarks/runs", response_model=list[BenchmarkRunView],
         dependencies=[Depends(require_api_key)])
async def list_benchmark_runs(limit: int = Query(default=20, ge=1, le=50),
                              dataset_id: str | None = Query(default=None, max_length=100)):
    query = select(BenchmarkRun)
    if dataset_id:
        query = query.where(BenchmarkRun.dataset_id == dataset_id)
    async with SessionLocal() as session:
        return (await session.scalars(query.order_by(
            BenchmarkRun.created_at.desc(), BenchmarkRun.id.desc()).limit(limit))).all()


@app.get("/api/benchmarks/compare", dependencies=[Depends(require_api_key)])
async def compare_benchmark_runs(baseline_id: str, candidate_id: str):
    """Compare identical dataset/model runs using ONLY common successfully scored IDs."""
    if baseline_id == candidate_id:
        raise HTTPException(422, "Choose two different benchmark runs")
    async with SessionLocal() as session:
        baseline = await session.get(BenchmarkRun, baseline_id)
        candidate = await session.get(BenchmarkRun, candidate_id)
    if baseline is None or candidate is None:
        raise HTTPException(404, "One or both benchmark runs not found")
    if baseline.dataset_sha256 != candidate.dataset_sha256 or baseline.dataset_id != candidate.dataset_id:
        raise HTTPException(409, "Dataset hashes differ; comparison would not be valid")
    if baseline.judge_model != candidate.judge_model:
        raise HTTPException(409, "Judge model names differ; this is not a matched-model comparison")
    if baseline.runner_version != PROFILES["baseline"] or candidate.runner_version not in {
        PROFILES["calibrated"], "2.1.0-intent-gated", "2.0.0-calibrated"
    }:
        raise HTTPException(422, "Choose a V5 baseline and a V6-family calibrated candidate")
    data, checksum = dataset_snapshot()
    if checksum != baseline.dataset_sha256:
        raise HTTPException(409, "Stored dataset version is unavailable; comparison cannot be revalidated")
    result = compare_paired_predictions(data["cases"], baseline.predictions_json or [],
                                         candidate.predictions_json or [])
    return {"baseline_id": baseline_id, "candidate_id": candidate_id,
            "baseline_status": baseline.status, "candidate_status": candidate.status,
            "dataset_sha256": checksum, "judge_model": baseline.judge_model,
            "baseline_runner": baseline.runner_version, "candidate_runner": candidate.runner_version,
            **result}


@app.get("/api/benchmarks/runs/{run_id}", response_model=BenchmarkRunView,
         dependencies=[Depends(require_api_key)])
async def get_benchmark_run(run_id: str):
    async with SessionLocal() as session:
        run = await session.get(BenchmarkRun, run_id)
        if run is None:
            raise HTTPException(404, "Benchmark run not found")
        return run


@app.post("/api/benchmarks/runs", response_model=BenchmarkRunView, status_code=202,
          dependencies=[Depends(require_api_key)])
async def queue_benchmark(profile: str = Query(default="calibrated", pattern="^(baseline|calibrated)$")):
    """Queue a pinned judge profile; never runs model requests in FastAPI."""
    if settings.llm_mode != "ollama":
        raise HTTPException(409, "Benchmark requires LLM_MODE=ollama")
    dataset, checksum = dataset_snapshot()
    async with SessionLocal() as session:
        # Coordinates legacy and V7 queueing to avoid racing new long GPU jobs.
        await session.execute(text("SELECT pg_advisory_xact_lock(418273)"))
        # Avoid accidentally queuing two long-running evaluations at once.
        active = await session.scalar(select(BenchmarkRun).where(
            BenchmarkRun.status.in_(["queued", "running"])).limit(1))
        if active:
            raise HTTPException(409, f"A benchmark is already active: {active.id}")
        run = BenchmarkRun(id=str(uuid.uuid4()), dataset_id=dataset["dataset_id"],
                           dataset_sha256=checksum, judge_model=settings.ollama_model,
                           runner_version=PROFILES[profile], status="queued",
                           total=len(dataset["cases"]), progress=0,
                           metrics_json=summarise_predictions(dataset["cases"], []),
                           predictions_json=[])
        session.add(run)
        await append_event(session, task_id=None, actor="api", action="benchmark_queued",
                           detail={"benchmark_id": run.id, "dataset_sha256": checksum,
                                   "model": run.judge_model, "runner_profile": profile})
        await session.commit()
        await session.refresh(run)
    try:
        await redis_client.lpush(BENCHMARK_QUEUE, run.id)
    except redis.RedisError:
        # The worker startup recovery picks up PostgreSQL queued runs.
        pass
    return run


# --- V7: FROZEN judge on new, separate 100-case holdout. ---
@app.get("/api/reliability/manifest", dependencies=[Depends(require_api_key)])
async def get_reliability_manifest():
    return holdout_manifest()


async def _study_report(study_id: str, *, bootstrap: bool = True) -> dict:
    async with SessionLocal() as session:
        study = await session.get(ReliabilityStudy, study_id)
        if study is None:
            raise HTTPException(404, "Reliability study not found")
        all_runs = (await session.scalars(select(BenchmarkRun).where(
            BenchmarkRun.id.in_(study.run_ids_json or [])))).all()
    run_map = {run.id: run for run in all_runs}
    runs = []
    for rid in study.run_ids_json or []:
        run = run_map.get(rid)
        if run is not None:
            runs.append({"id": run.id, "status": run.status, "progress": run.progress,
                         "runner_version": run.runner_version,
                         "predictions_json": run.predictions_json or []})
    try:
        dataset, digest = load_dataset(study.dataset_id)
    except (ValueError, FileNotFoundError):
        raise HTTPException(409, "Pinned holdout dataset no longer available") from None
    if digest != study.dataset_sha256:
        raise HTTPException(409, "Holdout checksum mismatch; results cannot be safely combined")
    report = reliability_report(dataset, {
        "id": study.id, "dataset_id": study.dataset_id, "dataset_sha256": study.dataset_sha256,
        "judge_model": study.judge_model,"runner_version":study.runner_version,
        "judge_source_sha256":study.judge_source_sha256,"repeats":study.repeats,
    }, runs, iterations=1200 if bootstrap else 0)
    report["created_at"] = study.created_at.isoformat()
    report["current_judge_sha256"] = freeze_hash()
    report["code_hash_matches_current"] = (freeze_hash() == study.judge_source_sha256)
    return report


@app.get("/api/reliability/studies", dependencies=[Depends(require_api_key)])
async def list_reliability_studies(limit: int = Query(default=12, ge=1, le=50)):
    async with SessionLocal() as session:
        rows = (await session.scalars(select(ReliabilityStudy)
                .order_by(ReliabilityStudy.created_at.desc()).limit(limit))).all()
    return [await _study_report(row.id,bootstrap=False) for row in rows]


@app.get("/api/reliability/studies/{study_id}", dependencies=[Depends(require_api_key)])
async def get_reliability_study(study_id: str):
    return await _study_report(study_id)


@app.get("/api/reliability/studies/{study_id}/report.md", dependencies=[Depends(require_api_key)])
async def download_reliability_markdown(study_id: str):
    report = await _study_report(study_id)
    return Response(content=markdown_report(report),media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition":f'attachment; filename="agentops-v7-{report["study_id"][:8]}.md"'})


@app.get("/api/reliability/studies/{study_id}/report.csv", dependencies=[Depends(require_api_key)])
async def download_reliability_csv(study_id: str):
    report = await _study_report(study_id,bootstrap=False)
    return Response(content=csv_report(report),media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":f'attachment; filename="agentops-v7-{report["study_id"][:8]}.csv"'})


@app.get("/api/reliability/studies/{study_id}/predictions.csv", dependencies=[Depends(require_api_key)])
async def download_reliability_predictions(study_id: str):
    async with SessionLocal() as session:
        study = await session.get(ReliabilityStudy, study_id)
        if not study:
            raise HTTPException(404, "Study not found")
        raw = (await session.scalars(select(BenchmarkRun).where(BenchmarkRun.id.in_(study.run_ids_json)))).all()
    by_id={run.id:run for run in raw}
    ordered=[{"id":by_id[rid].id,"predictions_json":by_id[rid].predictions_json or []}
             for rid in study.run_ids_json if rid in by_id]
    dataset, checksum=load_dataset(study.dataset_id)
    if checksum != study.dataset_sha256:
        raise HTTPException(409, "Holdout checksum mismatch; export disabled")
    return Response(content=detailed_predictions_csv(dataset,study.id,ordered),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":f'attachment; filename="agentops-v7-{study.id[:8]}-predictions.csv"'})


@app.post("/api/reliability/studies", status_code=202, dependencies=[Depends(require_api_key)])
async def queue_reliability_study(repeats: int = Query(default=3, ge=2, le=5)):
    """Single transaction queues 2/3/5 repetitions, as independent BenchmarkRun rows.

    They share dataset and judge implementation; no gold labels are sent to Ollama.
    """
    if repeats not in (2,3,5):
        raise HTTPException(422, "Supported repetitions: 2, 3, 5")
    if settings.llm_mode != "ollama":
        raise HTTPException(409, "V7 study requires LLM_MODE=ollama")
    data, checksum = load_dataset(HOLDOUT_ID)
    judge_hash = freeze_hash()
    ids = [str(uuid.uuid4()) for _ in range(repeats)]
    study = ReliabilityStudy(id=str(uuid.uuid4()), dataset_id=HOLDOUT_ID,
                             dataset_sha256=checksum, judge_model=settings.ollama_model,
                             runner_version=PROFILES['calibrated'], judge_source_sha256=judge_hash,
                             repeats=repeats, run_ids_json=ids)
    async with SessionLocal() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(418273)"))
        active = await session.scalar(select(BenchmarkRun).where(
            BenchmarkRun.status.in_(["queued", "running"])).limit(1))
        if active:
            raise HTTPException(409, f"A benchmark is already active: {active.id}")
        session.add(study)
        for rid in ids:
            session.add(BenchmarkRun(id=rid,dataset_id=HOLDOUT_ID,dataset_sha256=checksum,
                judge_model=settings.ollama_model,runner_version=PROFILES['calibrated'],
                status="queued",total=len(data['cases']),progress=0,
                predictions_json=[],metrics_json=summarise_predictions(data['cases'], [])))
        await append_event(session,task_id=None,actor="api",action="reliability_study_queued",
            detail={"study_id":study.id,"dataset_sha256":checksum,"repetitions":repeats,
                    "runner_version":study.runner_version,"judge_sha256":judge_hash})
        await session.commit()
    try:
        # RPUSH + BLPOP means first repetition executes first, one at a time.
        await redis_client.rpush(BENCHMARK_QUEUE,*ids)
    except redis.RedisError:
        # Queued DB records are recoverable by restarting the benchmark worker.
        pass
    return {"study_id":study.id,"status":"queued","run_ids":ids,"repeats":repeats,
            "dataset_sha256":checksum,"runner_version":study.runner_version,
            "judge_source_sha256":judge_hash}


# --- V8: human-in-the-loop reviews (separate from frozen V7 gold and judge) ---
class HumanDecisionInput(BaseModel):
    case_id: str = Field(min_length=1, max_length=30)
    role: Literal['reviewer1', 'reviewer2']
    reviewer_id: str = Field(min_length=2, max_length=80)
    label: Literal['supported', 'unsupported', 'contradicted', 'abstention']
    reason: str = Field(min_length=12, max_length=2000)
    confidence: int = Field(ge=1, le=5)


class HumanAdjudicationInput(BaseModel):
    case_id: str = Field(min_length=1, max_length=30)
    reviewer_id: str = Field(min_length=2, max_length=80)
    label: Literal['supported', 'unsupported', 'contradicted', 'abstention']
    reason: str = Field(min_length=12, max_length=2000)
    confidence: int = Field(ge=1, le=5)


async def _human_votes(session, dataset_sha: str):
    rows = (await session.scalars(select(HumanReviewDecision)
            .where(HumanReviewDecision.dataset_sha256 == dataset_sha)
            .order_by(HumanReviewDecision.created_at, HumanReviewDecision.id))).all()
    return current_decisions(rows)


# --- V8.1 single-reviewer mode, using existing reviewer1 votes unchanged ---
async def _single_review_rows(session, digest: str):
    return (await session.scalars(select(HumanReviewDecision)
            .where(HumanReviewDecision.dataset_sha256 == digest)
            .order_by(HumanReviewDecision.created_at, HumanReviewDecision.id))).all()


def _single_reviewer_id(reviewer_id: str) -> str:
    user = reviewer_id.strip()
    if not 2 <= len(user) <= 80:
        raise HTTPException(422, 'Reviewer ID must contain 2–80 characters')
    return user


async def _single_spreadsheet_import_batch(upload: UploadFile, person: str):
    """Read only reviewer_1 columns; never trust AI suggestion columns."""
    size_limited = await upload.read(MAX_UPLOAD_BYTES + 1)
    try:
        records, file_digest = parse_upload(size_limited, upload.filename or '')
    except ImportFormatError as exc:
        raise HTTPException(422, str(exc)) from exc
    data, digest, _ = review_dataset()
    return records, file_digest, data, digest


def _import_audit_case_ids(events, person: str, digest: str) -> set[str]:
    ids = set()
    for event in events:
        detail = event.detail_json or {}
        if detail.get('reviewer_id', '').casefold() == person.casefold() and detail.get('dataset_sha256') == digest:
            ids.update(detail.get('case_ids') or [])
    return ids


async def _spreadsheet_imported_ids(session, person: str, digest: str) -> set[str]:
    events = (await session.scalars(select(AuditEvent).where(
        AuditEvent.action == 'human_review_bulk_import'
    ).order_by(AuditEvent.id))).all()
    return _import_audit_case_ids(events, person, digest)


@app.post('/api/human-review/single/import/preview', dependencies=[Depends(require_api_key)])
async def preview_single_excel(file: UploadFile = File(...), reviewer_id: str = Form(...)):
    person = _single_reviewer_id(reviewer_id)
    records, file_digest, data, digest = await _single_spreadsheet_import_batch(file, person)
    async with SessionLocal() as session:
        original_rows = await _single_review_rows(session, digest)
    current = current_decisions(original_rows)
    slots = {case_id: votes['reviewer1'] for case_id, votes in current.items() if 'reviewer1' in votes}
    plan = prepare_import(records, data['cases'], slots, person)
    return {'version': IMPORT_VERSION, 'dataset_sha256': digest, 'reviewer_id': person,
            'file_sha256': file_digest, 'file_name': file.filename,
            'counts': plan['counts'], 'items': plan['rows'], 'warning': plan['warning'],
            'ready_to_commit': bool(plan['ready']) and not plan['counts']['error'] and not plan['counts']['locked']}


@app.post('/api/human-review/single/import/commit', dependencies=[Depends(require_api_key)])
async def commit_single_excel(file: UploadFile = File(...), reviewer_id: str = Form(...),
                              expected_sha256: str = Form(...), confirm: str = Form(...)):
    if confirm != 'IMPORT_REVIEWER_1_VOTES':
        raise HTTPException(422, 'Confirm the reviewed preview before importing')
    person = _single_reviewer_id(reviewer_id)
    records, file_digest, data, digest = await _single_spreadsheet_import_batch(file, person)
    if file_digest != expected_sha256:
        raise HTTPException(409, 'The uploaded file changed after preview; preview it again')
    async with SessionLocal() as session:
        # Match regular human-review submit's lock: concurrent imports do not duplicate votes.
        await session.execute(text('SELECT pg_advisory_xact_lock(418274)'))
        original_rows = await _single_review_rows(session, digest)
        current = current_decisions(original_rows)
        slots = {case_id: votes['reviewer1'] for case_id, votes in current.items() if 'reviewer1' in votes}
        plan = prepare_import(records, data['cases'], slots, person)
        if plan['counts']['error'] or plan['counts']['locked']:
            raise HTTPException(409, 'Import contains invalid or reviewer-locked rows; review preview and fix the file')
        if not plan['ready']:
            return {'version':IMPORT_VERSION, 'imported':0, 'already_recorded':plan['counts']['existing'],
                    'empty':plan['counts']['empty'], 'dataset_sha256':digest,
                    'message':'No new human votes found. Existing votes were preserved.'}
        ids = []
        for payload in plan['ready']:
            row = HumanReviewDecision(id=str(uuid.uuid4()), dataset_id=data['dataset_id'],
                                      dataset_sha256=digest, case_id=payload['case_id'],
                                      role='reviewer1', reviewer_id=person,
                                      label=payload['label'], reason=payload['reason'],
                                      confidence=payload['confidence'])
            session.add(row)
            ids.append(row.case_id)
        await session.flush()
        await append_event(session, task_id=None, actor='human_review', action='human_review_bulk_import',
                           detail={'case_ids':ids,'count':len(ids),'reviewer_id':person,
                                   'dataset_sha256':digest,'file_sha256':file_digest,
                                   'provenance':'spreadsheet_review_not_blinded','version':IMPORT_VERSION})
        await session.commit()
    return {'version':IMPORT_VERSION, 'imported':len(ids),
            'already_recorded':plan['counts']['existing'], 'empty':plan['counts']['empty'],
            'dataset_sha256':digest, 'message':'New votes appended. Existing votes not altered. Spreadsheet votes are not automatically blinded.'}


@app.get('/api/human-review/single/manifest', dependencies=[Depends(require_api_key)])
async def single_manifest():
    data, digest, _ = review_dataset()
    return {'version': SINGLE_REVIEW_VERSION, 'dataset_id':data['dataset_id'],
            'dataset_sha256':digest, 'total_cases':len(data['cases']),
            'review_role':'reviewer1', 'existing_votes_preserved':True,
            'original_judge_unchanged':True,
            'identity_warning':'Reviewer IDs are self-declared; one API key is shared.'}


@app.get('/api/human-review/single/cases', dependencies=[Depends(require_api_key)])
async def single_cases(reviewer_id: str = Query(min_length=2, max_length=80)):
    person = _single_reviewer_id(reviewer_id)
    data, digest, _ = review_dataset()
    async with SessionLocal() as session:
        rows = await _single_review_rows(session, digest)
    return single_queue(data, current_decisions(rows), single_vote_history(rows, person))


@app.get('/api/human-review/single/summary', dependencies=[Depends(require_api_key)])
async def single_review_summary(reviewer_id: str = Query(min_length=2, max_length=80)):
    person = _single_reviewer_id(reviewer_id)
    data, digest, suggestions = review_dataset()
    async with SessionLocal() as session:
        rows = await _single_review_rows(session, digest)
    async with SessionLocal() as session:
        imported = await _spreadsheet_imported_ids(session, person, digest)
    return {'dataset_sha256':digest, 'spreadsheet_imported_first_votes':len(imported),
            **single_summary(data, single_vote_history(rows, person), suggestions)}


@app.get('/api/human-review/single/cases/{case_id}', dependencies=[Depends(require_api_key)])
async def single_review_case(case_id: str, reviewer_id: str = Query(min_length=2, max_length=80)):
    person = _single_reviewer_id(reviewer_id)
    data, digest, suggestions = review_dataset()
    case = next((x for x in data['cases'] if x['case_id'] == case_id), None)
    if case is None:
        raise HTTPException(404, 'Unknown holdout case')
    async with SessionLocal() as session:
        rows = await _single_review_rows(session, digest)
    owned = single_vote_history(rows, person).get(case_id)
    original = current_decisions(rows).get(case_id, {}).get('reviewer1')
    return single_case_payload(case, suggestions[case_id], owned, locked=bool(original and not owned))


@app.get('/api/human-review/single/export.{kind}', dependencies=[Depends(require_api_key)])
async def single_review_export(kind: Literal['csv','json'],
                               reviewer_id: str = Query(min_length=2, max_length=80)):
    person = _single_reviewer_id(reviewer_id)
    data, digest, suggestions = review_dataset()
    async with SessionLocal() as session:
        rows = await _single_review_rows(session, digest)
        imported = await _spreadsheet_imported_ids(session, person, digest)
    content = single_export(data, single_vote_history(rows, person), suggestions, kind, imported_case_ids=imported)
    return Response(content=content, media_type='text/csv; charset=utf-8' if kind=='csv' else 'application/json; charset=utf-8',
                    headers={'Content-Disposition':f'attachment; filename="agentops-v8_1-single-review.{kind}"',
                             'Cache-Control':'no-store'})


@app.get('/api/human-review/single/compare/{study_id}', dependencies=[Depends(require_api_key)])
async def single_review_compare(study_id: str,
                                reviewer_id: str = Query(min_length=2, max_length=80)):
    person = _single_reviewer_id(reviewer_id)
    data, digest, _ = review_dataset()
    async with SessionLocal() as session:
        rows = await _single_review_rows(session, digest)
        owned = single_vote_history(rows, person)
        study = await session.get(ReliabilityStudy, study_id)
        if study is None:
            raise HTTPException(404, 'Reliability study not found')
        if study.dataset_sha256 != digest or study.dataset_id != data['dataset_id']:
            raise HTTPException(409, 'Cannot compare different dataset IDs/hashes')
        imported = await _spreadsheet_imported_ids(session, person, digest)
        runs = []
        for rid in (study.run_ids_json or []):
            run = await session.get(BenchmarkRun, rid)
            if run and run.dataset_sha256 == digest:
                runs.append(run)
    return {'study_id':study_id, 'dataset_sha256':digest,
            'human_reviewed_count':len(owned),
            'spreadsheet_imported_first_votes':len(imported),
            'results':[{'run_id':r.id, **single_model_comparison(data,owned,r.predictions_json or [])}
                       for r in runs],
            'warning':'Primary reference is the first recorded reviewer-1 vote. Spreadsheet-imported votes are not independently blinded. No second reviewer or Cohen kappa; partial until 100 cases reviewed.'}


@app.get('/api/human-review/manifest', dependencies=[Depends(require_api_key)])
async def human_review_manifest():
    data, digest, suggestions = review_dataset()
    return {'review_version':V8_REVIEW_VERSION, 'dataset_id': data['dataset_id'], 'dataset_sha256':digest,
            'cases':len(data['cases']), 'ai_suggestions':len(suggestions),
            'prior_workbook_drafts':sum(bool(v.get('prior_workbook_draft')) for v in suggestions.values()),
            'human_reviews_seeded':0, 'status':'human decisions remain separate and append-only',
            'labels':list(HUMAN_LABELS),
            'blinding_note':'Other reviewer labels and AI suggestions are hidden until two decisions exist. '
                            'Self-declared reviewer roles on a shared API key do not enforce independent identities.'}


@app.get('/api/human-review/cases', dependencies=[Depends(require_api_key)])
async def human_review_cases():
    data, digest, _ = review_dataset()
    async with SessionLocal() as session:
        votes = await _human_votes(session, digest)
    return safe_review_list(data, votes)


@app.get('/api/human-review/summary', dependencies=[Depends(require_api_key)])
async def human_review_summary():
    data, digest, suggestions = review_dataset()
    async with SessionLocal() as session:
        votes = await _human_votes(session, digest)
    return {'dataset_sha256':digest, **review_summary(data, votes, suggestions)}


@app.get('/api/human-review/cases/{case_id}', dependencies=[Depends(require_api_key)])
async def human_review_case(case_id: str, role: Literal['reviewer1','reviewer2','adjudicator'] = 'reviewer1',
                            reviewer_id: str = Query(default='', max_length=80)):
    data, digest, suggestions = review_dataset()
    case = next((c for c in data['cases'] if c['case_id'] == case_id), None)
    if not case:
        raise HTTPException(404, 'Unknown review case')
    async with SessionLocal() as session:
        votes = await _human_votes(session, digest)
    return safe_review_case(case, votes.get(case_id, {}), suggestions[case_id], role, reviewer_id.strip())


@app.post('/api/human-review/decisions', status_code=201, dependencies=[Depends(require_api_key)])
async def submit_human_decision(payload: HumanDecisionInput):
    data, digest, _ = review_dataset()
    valid_ids = {c['case_id'] for c in data['cases']}
    if payload.case_id not in valid_ids:
        raise HTTPException(404, 'Unknown review case')
    reviewer = payload.reviewer_id.strip()
    reason = payload.reason.strip()
    if len(reviewer) < 2 or len(reason) < 12:
        raise HTTPException(422, 'Reviewer ID and evidence-based reason required')
    async with SessionLocal() as session:
        # Serialize decision changes, including concurrent second-reviewer requests.
        await session.execute(text('SELECT pg_advisory_xact_lock(418274)'))
        votes = (await _human_votes(session, digest)).get(payload.case_id,{})
        if 'adjudicator' in votes:
            raise HTTPException(409, 'This conflict was adjudicated; decisions are locked for audit integrity')
        prior = votes.get(payload.role)
        other = votes.get('reviewer2' if payload.role == 'reviewer1' else 'reviewer1')
        if prior and prior['reviewer_id'].casefold() != reviewer.casefold():
            raise HTTPException(409, 'This reviewer slot belongs to another reviewer ID')
        if other and other['reviewer_id'].casefold() == reviewer.casefold():
            raise HTTPException(409, 'Two independent slots cannot use the same reviewer ID')
        row=HumanReviewDecision(id=str(uuid.uuid4()), dataset_id=data['dataset_id'],
                                dataset_sha256=digest, case_id=payload.case_id,role=payload.role,
                                reviewer_id=reviewer,label=payload.label,reason=reason,
                                confidence=payload.confidence)
        session.add(row)
        await append_event(session,task_id=None,actor='human_review',action='human_review_saved',
                           detail={'case_id':row.case_id,'role':row.role,'decision_id':row.id,
                                   'revision':bool(prior),'dataset_sha256':digest})
        await session.commit()
    return {'saved':True, 'decision_id':row.id, 'case_id':row.case_id,'role':row.role,
            'next_step':'Second independent review or final adjudication'}


@app.post('/api/human-review/adjudicate', status_code=201, dependencies=[Depends(require_api_key)])
async def submit_human_adjudication(payload: HumanAdjudicationInput):
    data, digest, _ = review_dataset()
    if payload.case_id not in {c['case_id'] for c in data['cases']}:
        raise HTTPException(404,'Unknown review case')
    reviewer=payload.reviewer_id.strip();reason=payload.reason.strip()
    if len(reviewer)<2 or len(reason)<12:
        raise HTTPException(422,'Third reviewer and evidence-based reason required')
    async with SessionLocal() as session:
        await session.execute(text('SELECT pg_advisory_xact_lock(418274)'))
        votes=(await _human_votes(session,digest)).get(payload.case_id,{})
        if 'adjudicator' in votes:
            raise HTTPException(409,'Already adjudicated: append-only final decision cannot be overwritten')
        if not all(x in votes for x in ('reviewer1','reviewer2')):
            raise HTTPException(409,'Two human reviews are required before adjudication')
        if votes['reviewer1']['label']==votes['reviewer2']['label']:
            raise HTTPException(409,'Both reviewers agree; no adjudication is necessary')
        if reviewer.casefold() in (votes['reviewer1']['reviewer_id'].casefold(),
                                  votes['reviewer2']['reviewer_id'].casefold()):
            raise HTTPException(409,'Adjudicator must be a distinct third reviewer')
        row=HumanReviewDecision(id=str(uuid.uuid4()),dataset_id=data['dataset_id'],
                                dataset_sha256=digest,case_id=payload.case_id,role='adjudicator',
                                reviewer_id=reviewer,label=payload.label,reason=reason,
                                confidence=payload.confidence)
        session.add(row)
        await append_event(session,task_id=None,actor='human_review',action='human_review_adjudicated',
                           detail={'case_id':row.case_id,'decision_id':row.id,'dataset_sha256':digest})
        await session.commit()
    return {'saved':True,'decision_id':row.id,'case_id':row.case_id,'final_label':row.label}


@app.get('/api/human-review/export.csv', dependencies=[Depends(require_api_key)])
async def human_review_export_csv():
    data,digest,suggestions=review_dataset()
    async with SessionLocal() as session:
        votes=await _human_votes(session,digest)
    content=export_human_review_csv(data,votes,suggestions)
    return Response(content=content,media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':'attachment; filename="agentops-v8-human-review.csv"'})


@app.get('/api/human-review/compare/{study_id}', dependencies=[Depends(require_api_key)])
async def human_review_compare(study_id:str):
    """Aggregate only. Original holdout gold is never exposed in the reviewer UI."""
    data,digest,_=review_dataset()
    async with SessionLocal() as session:
        votes=await _human_votes(session,digest)
        study=await session.get(ReliabilityStudy,study_id)
        if not study:
            raise HTTPException(404,'V7 reliability study not found')
        if study.dataset_sha256 != digest or study.dataset_id != data['dataset_id']:
            raise HTTPException(409,'Cannot compare different dataset hashes')
        runids=study.run_ids_json or []
        runs=[]
        for rid in runids:
            run=await session.get(BenchmarkRun,rid)
            if run is not None and run.dataset_sha256==digest:
                runs.append(run)
    final={case_id:resolve_case(decisions)['final_label'] for case_id,decisions in votes.items()
           if resolve_case(decisions)['final_label']}
    return {'study_id':study_id,'dataset_sha256':digest,'finalized_count':len(final),
            'results':[{'run_id':r.id,**human_vs_model(final,r.predictions_json or [],data['cases'])} for r in runs],
            'warning':'These are provisional metrics on independently adjudicated cases only. '
                      'AI suggestions and prior spreadsheet drafts do not count. No gold relabeling.'}

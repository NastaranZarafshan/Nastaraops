from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MemoryEntry, Task


async def collect_metrics(session: AsyncSession) -> dict:
    row = (await session.execute(select(
        func.count(Task.id).label("total"),
        func.sum(case((Task.status == "completed", 1), else_=0)).label("completed"),
        func.sum(case((Task.status == "awaiting_approval", 1), else_=0)).label("awaiting_approval"),
        func.sum(case((Task.status == "rejected", 1), else_=0)).label("rejected"),
        func.sum(case((Task.status == "failed", 1), else_=0)).label("failed"),
        func.sum(case((Task.status.in_(["queued", "running", "resume_queued"]), 1), else_=0)).label("active"),
        func.avg(Task.duration_ms).label("avg_duration_ms"),
    ))).one()
    by_kind = (await session.execute(select(Task.kind, func.count(Task.id)).group_by(Task.kind))).all()
    memory_count = await session.scalar(select(func.count(MemoryEntry.id)))
    total = row.total or 0
    completed = row.completed or 0
    return {"total": total, "completed": completed,
            "awaiting_approval": row.awaiting_approval or 0,
            "rejected": row.rejected or 0, "failed": row.failed or 0,
            "active": row.active or 0, "memory_count": memory_count or 0,
            "completion_rate_pct": round(100 * completed / total, 1) if total else 0,
            "avg_duration_ms": round(float(row.avg_duration_ms or 0), 1),
            "by_kind": {kind: count for kind, count in by_kind}}

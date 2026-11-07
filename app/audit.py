"""Append-only, hash-chained audit records (tamper evident, not immutable)."""

import hashlib
import json

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, utcnow

GENESIS = "0" * 64
ADVISORY_LOCK = 418271


def hash_event(previous: str, *, task_id: str | None, actor: str, action: str,
               detail: dict, created_at: str) -> str:
    payload = {"previous": previous, "task_id": task_id, "actor": actor,
               "action": action, "detail": detail, "created_at": created_at}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def append_event(session: AsyncSession, *, task_id: str | None, actor: str,
                       action: str, detail: dict | None = None) -> AuditEvent:
    # Serialize all append operations across API/worker processes to prevent hash-chain forks.
    await session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": ADVISORY_LOCK})
    previous = await session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    event = AuditEvent(task_id=task_id, actor=actor, action=action,
                       detail_json=detail or {}, created_at=utcnow(),
                       prev_hash=previous.event_hash if previous else GENESIS,
                       event_hash="")
    event.event_hash = hash_event(event.prev_hash, task_id=task_id, actor=actor,
                                  action=action, detail=event.detail_json,
                                  created_at=event.created_at.isoformat())
    session.add(event)
    await session.flush()
    return event


async def verify_chain(session: AsyncSession) -> tuple[bool, int, int | None]:
    events = (await session.scalars(select(AuditEvent).order_by(AuditEvent.id.asc()))).all()
    previous = GENESIS
    for event in events:
        expected = hash_event(previous, task_id=event.task_id, actor=event.actor,
                              action=event.action, detail=event.detail_json,
                              created_at=event.created_at.isoformat())
        if event.prev_hash != previous or event.event_hash != expected:
            return False, len(events), event.id
        previous = event.event_hash
    return True, len(events), None

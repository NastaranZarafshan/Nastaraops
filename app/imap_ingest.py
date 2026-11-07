"""Optional read-only IMAP scanner; creates email tasks, never sends messages."""

import asyncio
import email
import imaplib
import logging
import uuid
from email.header import decode_header, make_header
from email.message import Message

import redis.asyncio as redis
from sqlalchemy.exc import IntegrityError

from app.audit import append_event
from app.config import get_settings
from app.db import SessionLocal, create_schema, engine
from app.models import IngestedEmail, Task
from app.worker import QUEUE

log = logging.getLogger("imap_ingest")


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, UnicodeError):
        return value


def plain_text(message: Message) -> str:
    chunks = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_type() != "text/plain" or part.get_content_disposition() == "attachment":
            continue
        data = part.get_payload(decode=True)
        if data is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            chunks.append(data.decode(charset, errors="replace"))
        except LookupError:
            chunks.append(data.decode("utf-8", errors="replace"))
    return "\n".join(chunks)[:30000].strip()


def fetch_unseen() -> list[dict]:
    """Blocking IMAP I/O runs in a thread. BODY.PEEK does not mark mail as read."""
    settings = get_settings()
    with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port, timeout=20) as mailbox:
        mailbox.login(settings.imap_username, settings.imap_password)
        status, _ = mailbox.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Cannot select inbox")
        uidvalidity = mailbox.response("UIDVALIDITY")[1]
        validity = uidvalidity[0].decode() if uidvalidity and uidvalidity[0] else "unknown"
        status, results = mailbox.uid("SEARCH", None, "UNSEEN")
        if status != "OK":
            raise RuntimeError("IMAP search failed")
        output = []
        for uid in (results[0] or b"").split()[-20:]:
            status, data = mailbox.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK":
                continue
            raw = next((part[1] for part in data if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
            if raw is None:
                continue
            msg = email.message_from_bytes(raw)
            content = plain_text(msg)
            if not content:
                continue
            output.append({"uid": f"{settings.imap_username}:{validity}:{uid.decode()}",
                           "title": decode_mime_header(msg.get("Subject"))[:180] or "(No subject)",
                           "sender": decode_mime_header(msg.get("From"))[:250], "content": content})
        mailbox.logout()
        return output


async def ingest_one(message: dict, r: redis.Redis) -> bool:
    task_id = str(uuid.uuid4())
    try:
        async with SessionLocal() as session:
            session.add(Task(id=task_id, kind="email", status="queued", title=message["title"],
                             content=message["content"],
                             metadata_json={"source": "imap", "from": message["sender"]}))
            session.add(IngestedEmail(source_uid=message["uid"], task_id=task_id))
            await session.flush()
            await append_event(session, task_id=task_id, actor="imap", action="email_ingested",
                               detail={"source": "imap"})
            await session.commit()
    except IntegrityError:
        # Email UID already recorded; no duplicate workflow.
        return False
    try:
        await r.lpush(QUEUE, task_id)
    except redis.RedisError:
        log.warning("Redis unavailable; task persists in Postgres for recovery: %s", task_id)
    return True


async def main():
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if not all((settings.imap_host, settings.imap_username, settings.imap_password)):
        raise SystemExit("IMAP_HOST, IMAP_USERNAME and IMAP_PASSWORD are required for mail profile")
    await create_schema()
    r = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        while True:
            try:
                messages = await asyncio.to_thread(fetch_unseen)
                created = sum([await ingest_one(message, r) for message in messages])
                log.info("IMAP checked %s messages; %s queued", len(messages), created)
            except Exception:
                log.exception("IMAP poll failed; retry on next interval")
            await asyncio.sleep(settings.imap_poll_seconds)
    finally:
        await r.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

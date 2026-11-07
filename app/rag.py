"""Local multilingual RAG using Ollama embeddings and Qdrant's REST API."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx

from app.config import get_settings

_collection_lock = asyncio.Lock()


def chunk_text(text: str, *, size: int | None = None, overlap: int | None = None) -> list[str]:
    """Split text into bounded, overlapping chunks without dropping content."""
    settings = get_settings()
    size = size or settings.rag_chunk_size
    overlap = settings.rag_chunk_overlap if overlap is None else overlap
    if overlap >= size:
        raise ValueError("RAG chunk overlap must be smaller than chunk size")

    normalized = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    normalized = "\n".join(line for line in normalized.split("\n") if line.strip()).strip()
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        if end < len(normalized):
            boundary = max(normalized.rfind("\n", start, end), normalized.rfind(". ", start, end),
                           normalized.rfind("? ", start, end), normalized.rfind("! ", start, end))
            if boundary > start + int(size * 0.55):
                end = boundary + (1 if normalized[boundary] == "\n" else 2)
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(normalized):
            break
        start = max(start + 1, end - overlap)
    return chunks


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    settings = get_settings()
    payload = {"model": settings.ollama_embed_model, "input": texts, "truncate": True}
    async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as client:
        response = await client.post(settings.ollama_base_url.rstrip("/") + "/api/embed", json=payload)
        response.raise_for_status()
        data = response.json()
    vectors = data.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise RuntimeError("Ollama returned an unexpected embeddings response")
    if not vectors or not isinstance(vectors[0], list) or not vectors[0]:
        raise RuntimeError("Ollama returned an empty embedding vector")
    return vectors


async def _collection_info(client: httpx.AsyncClient, collection: str) -> dict[str, Any] | None:
    # Qdrant may still be initializing immediately after Docker Compose starts it.
    for attempt in range(5):
        try:
            response = await client.get(f"/collections/{collection}")
            if response.status_code == 404:
                return None
            if response.status_code in {502, 503, 504} and attempt < 4:
                await asyncio.sleep(1 + attempt)
                continue
            response.raise_for_status()
            return response.json().get("result", {})
        except (httpx.ConnectError, httpx.TimeoutException):
            if attempt == 4:
                raise
            await asyncio.sleep(1 + attempt)
    return None


async def _ensure_collection(client: httpx.AsyncClient, vector_size: int) -> None:
    settings = get_settings()
    async with _collection_lock:
        info = await _collection_info(client, settings.qdrant_collection)
        if info is None:
            response = await client.put(
                f"/collections/{settings.qdrant_collection}",
                json={"vectors": {"size": vector_size, "distance": "Cosine"}},
            )
            if response.status_code not in {200, 201, 409}:
                response.raise_for_status()
            info = await _collection_info(client, settings.qdrant_collection)
        vector_config = (info or {}).get("config", {}).get("params", {}).get("vectors", {})
        if isinstance(vector_config, dict) and "size" in vector_config:
            actual_size = vector_config.get("size")
        elif isinstance(vector_config, dict) and isinstance(vector_config.get("default"), dict):
            actual_size = vector_config["default"].get("size")
        else:
            actual_size = None
        if actual_size is not None and actual_size != vector_size:
            raise RuntimeError(
                f"Qdrant collection vector size is {actual_size}, but embedding model "
                f"'{settings.ollama_embed_model}' returned {vector_size}. Use a new collection name "
                "or recreate the empty knowledge collection."
            )


async def index_document(
    *, document_id: str, title: str, filename: str, pages: list[tuple[str, int | None]],
) -> int:
    """Embed and index page-aware text chunks. Returns the number of stored chunks."""
    settings = get_settings()
    rows: list[dict[str, Any]] = []
    global_chunk_index = 0
    for page_text, page_number in pages:
        for chunk in chunk_text(page_text):
            rows.append({
                "document_id": document_id,
                "title": title,
                "filename": filename,
                "page": page_number,
                "chunk_index": global_chunk_index,
                "text": chunk,
            })
            global_chunk_index += 1
    if not rows:
        raise ValueError("No text chunks were extracted from the document")

    stored_count = 0
    async with httpx.AsyncClient(base_url=settings.qdrant_url.rstrip("/"),
                                 timeout=settings.model_timeout_seconds) as client:
        batch_size = 16
        upsert_batch: list[dict[str, Any]] = []
        collection_ready = False
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            vectors = await embed_texts([row["text"] for row in batch])
            if not collection_ready:
                await _ensure_collection(client, len(vectors[0]))
                collection_ready = True
            for row, vector in zip(batch, vectors, strict=True):
                upsert_batch.append({"id": str(uuid.uuid4()), "vector": vector, "payload": row})
            if len(upsert_batch) >= 128 or start + batch_size >= len(rows):
                response = await client.put(
                    f"/collections/{settings.qdrant_collection}/points?wait=true",
                    json={"points": upsert_batch},
                )
                response.raise_for_status()
                stored_count += len(upsert_batch)
                upsert_batch = []
    return stored_count


async def search_knowledge(query: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    settings = get_settings()
    query = query.strip()
    if not query:
        return []
    async with httpx.AsyncClient(base_url=settings.qdrant_url.rstrip("/"),
                                 timeout=settings.model_timeout_seconds) as client:
        info = await _collection_info(client, settings.qdrant_collection)
        if info is None or info.get("points_count") == 0:
            return []
        vector = (await embed_texts([query]))[0]
        response = await client.post(
            f"/collections/{settings.qdrant_collection}/points/query",
            json={"query": vector, "limit": limit or settings.rag_top_k,
                  "with_payload": True, "score_threshold": settings.rag_score_threshold},
        )
        response.raise_for_status()
        points = response.json().get("result", {}).get("points", [])
    matches: list[dict[str, Any]] = []
    for point in points:
        payload = point.get("payload") or {}
        matches.append({
            "source_id": str(point.get("id")),
            "score": round(float(point.get("score") or 0), 4),
            "document_id": payload.get("document_id"),
            "title": payload.get("title"),
            "filename": payload.get("filename"),
            "page": payload.get("page"),
            "chunk_index": payload.get("chunk_index"),
            "text": payload.get("text", ""),
        })
    return matches


async def delete_document_points(document_id: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.qdrant_url.rstrip("/"),
                                 timeout=settings.model_timeout_seconds) as client:
        info = await _collection_info(client, settings.qdrant_collection)
        if info is None:
            return
        response = await client.post(
            f"/collections/{settings.qdrant_collection}/points/delete?wait=true",
            json={"filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]}},
        )
        response.raise_for_status()

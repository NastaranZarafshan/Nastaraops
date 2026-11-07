"""Allowlisted call to a trusted internal MCP tool (no arbitrary tool execution)."""

import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import get_settings


async def lookup_policy(kind: str) -> str:
    if kind not in {"email", "document", "request", "report"}:
        raise ValueError("Invalid workflow kind")
    # The internal MCP service may still be starting when the worker receives a task.
    # Retry briefly; never silently bypass the policy tool on failure.
    for attempt in range(4):
        try:
            async with streamable_http_client(get_settings().mcp_url) as (reader, writer, *_):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    result = await session.call_tool("policy_lookup", {"workflow_type": kind})
                    if result.isError:
                        raise RuntimeError("MCP policy tool returned an error")
                    text = " ".join(str(item.text) for item in result.content
                                    if getattr(item, "type", "") == "text")[:1500]
                    if not text:
                        raise RuntimeError("MCP policy tool returned empty guidance")
                    return text
        except Exception:
            if attempt == 3:
                raise
            await asyncio.sleep(1.5)
    raise RuntimeError("MCP service did not respond")

# Local development operations

## Launch (Windows PowerShell)

Use Python 3.11 and Docker Desktop. Start with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

The Python API, MCP policy server, and both background workers run as native processes from `.venv`. Docker runs only PostgreSQL, Redis and Qdrant.

## URLs and logs

- Dashboard: http://127.0.0.1:8010
- Swagger UI: http://127.0.0.1:8010/docs
- Health: http://127.0.0.1:8010/health
- Readiness: http://127.0.0.1:8010/ready
- Python process logs: `.local/*.log`
- Infrastructure: `docker compose ps` and `docker compose logs --tail=60`

## Stop

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop.ps1
```

The stop script preserves all three named Docker volumes. Never use `docker compose down -v` for a working installation.

## Initial isolation

Compose project name: `nastaraops-native`. The local ports are PostgreSQL `15432`, Redis `16379`, Qdrant `16333`, MCP `8001`, API `8010`. These settings avoid clashing with the existing `enterprise-agent-ops` installation and the earlier `nastaraops` Compose project. Do not point the clean deployment at old production data.

## Real models and RAG

The default `LLM_MODE=mock` is deliberate; it tests orchestration without model downloads. To enable a locally installed Ollama, install it on Windows, pull `qwen2.5:3b` and `bge-m3`, change `LLM_MODE=ollama` in `.env`, stop all services, and restart. Install and configure Ollama separately; it is not bundled and requires downloads.

## Network / pip issues

The clean setup deliberately does not upgrade pip and uses timeouts, retries and cached wheels. It still needs working connectivity to PyPI. A `ReadTimeoutError` is a network problem, not proof that the project or dependency is broken. You can rerun `setup.ps1` to retry without deleting `.venv`.

## Security

The API binds to loopback only. Credentials are created in an ignored `.env` and must never be committed. Browser users manually enter the local `API_KEY`. This is a development workspace, not a production deployment.

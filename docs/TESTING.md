# Testing NastaraOps

**Maintainer:** Nastaran Zarafshan

The current **41-test** suite validates isolated behavior of the source code; the observed Windows run was `41 passed in 3.08s`. It does not execute against an active customer database, call real Ollama inference, or write workflow/audit entries to the running installation.

## Local prerequisites

Use the existing Python 3.11 environment created by `scripts/setup.ps1`. Do not delete `.env` or Docker volumes.

```powershell
cd D:\projects\NastaraOps
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
```

If package downloads time out, use a trusted accessible package index and longer pip network timeouts. Installing dependencies is independent from starting Docker services.

## Run the complete suite

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
```

For a focused check:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_grounding.py tests\test_chunking.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_policy.py tests\test_audit.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_api.py tests\test_workflow.py
```

## What is covered?

| Suite | Boundary |
|:--|:--|
| Grounding and chunking | Deterministic evidence-based output and text segmenting |
| Policy | Specialist routing, explicit approvals and sensitive action rules |
| Audit | Event hash construction (not the live persisted chain) |
| FastAPI | Public health and UI, authentication failures, validation errors; application startup bypassed |
| LangGraph | In-memory checkpoint, interrupt, reject/approve resume; simulated policy and inference |

**Not covered:** live Ollama inference quality, real Redis/PostgreSQL/Qdrant integration, long-running queues, database migration safety, real email sending, security penetration testing, or production uptime.

## Continuous integration

The workflow at `.github/workflows/ci.yml` is triggered by pushes and pull requests to `main`, and may also be run manually from GitHub Actions. It uses an ephemeral Ubuntu runner with Python 3.11, installs `requirements-test.txt`, checks imports/dependency consistency, compiles Python files, verifies frozen research/branding, and runs `pytest`.

The CI workflow needs **no GitHub secrets** and **does not provision Docker services**. No tests are allowed to connect to the actual NastaraOps database or process user review records.

The workflow currently omits Ruff to avoid reintroducing broad rules without a curated configuration. Add targeted linting in a later, separate improvement and confirm a clean local run before requiring it in CI.

If CI is red, open the failing job/step and inspect the actual traceback. A local green result does not guarantee green runners until the CI run has completed.

## Release checks

```powershell
.\.venv\Scripts\python.exe scripts\check_research.py
.\.venv\Scripts\python.exe scripts\check_nastaraops_branding.py
```

Before any public push, inspect staged filenames and content for secrets:

```powershell
git diff --cached --name-only
git diff --cached --check
git diff --cached
```

These static checks are useful but cannot certify production security. Future database-backed integration and end-to-end tests must use an explicitly isolated, disposable test environment.

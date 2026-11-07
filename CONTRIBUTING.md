# Contributing to NastaraOps

NastaraOps is an open, Python 3.11 local-first research and engineering reference project maintained by **Nastaran Zarafshan**.

## Development workflow

1. Create a feature branch from `main`.
2. Work in the local Python 3.11 `.venv`; keep PostgreSQL/Redis/Qdrant data isolated.
3. Run the complete test suite and provenance checks:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest -q tests
   .\.venv\Scripts\python.exe scripts\check_research.py
   .\.venv\Scripts\python.exe scripts\check_nastaraops_branding.py
   ```

4. Inspect staged files and the diff for private information before committing.
5. Open a pull request describing the behavior changed, tests performed, and operational risks.

## Privacy and integrity

Never commit `.env`, credentials, human reviewer exports or persisted Docker volumes. Do not alter frozen benchmark source/data or recalculate published historical metrics without a documented new research protocol and provenance. Keep simulated examples distinct from real operating claims.

The current automated suite is deliberately service-free. Database or external-action tests must use disposable fixtures and must never contact the running user installation.

See [Testing](docs/TESTING.md), [Engineering roadmap](docs/WORKFLOW_PLAN.md), and [Security](SECURITY.md).

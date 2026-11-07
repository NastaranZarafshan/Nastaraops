# NastaraOps — Engineering Quality Roadmap

The project is developed in small, independently verifiable stages.

## Completed locally

1. **Native Windows Python setup** — application libraries installed into Python 3.11 `.venv`; Docker used for infrastructure.
2. **Workflow execution** — local LangGraph request workflow reached `completed`.
3. **Human oversight** — observed approval and rejection flows and audit verification.
4. **Local model inference** — Qwen 2.5 3B produced a customer support draft through Ollama.
5. **Grounding and abstention** — retrieved demo policy was cited, and unsupported pricing/warranty claims were not invented in observed examples.
6. **Automated tests** — 41 isolated test cases passed locally (`41 passed in 3.08s`).

These items are observations about a development environment, not production reliability claims.

## Current milestone: CI and developer experience

- Publish `.github/workflows/ci.yml` and verify the job **actually passes on GitHub**.
- Maintain a precise README, security disclosure and setup notes.
- Keep the API key, `.env`, review exports and persistent databases outside Git history.

## Future milestones

1. **Targeted lint and typing** — define a narrowly scoped Ruff configuration; introduce rules progressively, rather than breaking the pipeline with global defaults.
2. **Disposable integration tests** — use temporary PostgreSQL, Redis and Qdrant data; never reuse real local volumes.
3. **E2E browser tests** — verify the user journey from form submission to the audit trail.
4. **Evaluation harness** — create new, explicitly labeled test sets separate from historical frozen metrics; track source attribution, unsupported-question detection and hallucination indicators.
5. **Release process** — add reproducible environment constraints, dependency and secret scanning, documentation checks, and GitHub Releases when the pipeline is consistently green.

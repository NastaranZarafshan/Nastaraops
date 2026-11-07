# Security policy and scope

NastaraOps is a **local demonstration and research platform**, not a security-audited enterprise service.

- The API binds to `127.0.0.1` by default. Do not publicly expose it without additional authentication, TLS, network segmentation, and a threat model.
- The shared `X-API-Key` is a development mechanism, not tenant-aware access control.
- Protect `.env`, PostgreSQL volumes, worker queues, raw annotations, and audit exports.
- Do not publish private reviewer datasets or live email content.
- Never commit credentials. Review changes and scan source for secrets before GitHub publication.
- Agents may propose actions; external side effects are not automatically executed in this reference implementation.
- Keep the historical benchmark report's methodological limits attached to any reported metrics.

For security issues, contact the repository owner privately rather than posting exploit data in public issues.

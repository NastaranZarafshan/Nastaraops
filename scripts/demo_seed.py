"""Create a few synthetic workflows through the protected API (uses stdlib only)."""

import json
import os
import urllib.request


EXAMPLES = [
    {"kind": "email", "title": "Question about onboarding",
     "content": "Hello, can you send us the onboarding checklist and meeting options?", "metadata": {}},
    {"kind": "document", "title": "Supplier summary",
     "content": "Vendor: Northwind Ltd. Contract date: 2026-10-01. Total: $650. Delivery: 20 days.", "metadata": {}},
    {"kind": "request", "title": "Sensitive customer refund",
     "content": "Urgent: please refund $2500 to the customer and update their bank account.",
     "metadata": {"amount": 2500, "requested_action": "issue_refund"}},
    {"kind": "report", "title": "Operations performance",
     "content": "Summarize our workflow throughput and approval backlog.", "metadata": {}},
]


def main():
    key = os.environ.get("API_KEY")
    if not key:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8-sig").splitlines():
                if line.startswith("API_KEY="):
                    key = line.partition("=")[2].strip()
                    break
    if not key:
        raise SystemExit("Local .env with API_KEY is required. Run scripts/setup.ps1 first.")
    base = os.environ.get("BASE_URL", "http://127.0.0.1:8010").rstrip("/")
    for task in EXAMPLES:
        data = json.dumps(task).encode("utf-8")
        request = urllib.request.Request(base + "/api/tasks", data=data,
            headers={"X-API-Key": key, "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            parsed = json.load(response)
        print(task["kind"], parsed["id"], parsed["status"])


if __name__ == "__main__":
    main()

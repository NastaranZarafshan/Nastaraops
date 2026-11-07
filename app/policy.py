"""Deterministic authorization policy, independent of language model output."""

import re
from typing import Any

SENSITIVE_PATTERN = re.compile(
    r"\b(delete|erase|purge|wire transfer|bank account|payroll|invoice payment|"
    r"refund|password|credentials|ssn|social security|external send|confidential)\b"
    r"|حذف|پرداخت|اطلاعات بانکی|رمز عبور|محرمانه|بازپرداخت|حقوق و دستمزد",
    flags=re.IGNORECASE,
)
CONTROLLED_ACTIONS = {"send_email", "delete_record", "issue_refund", "transfer_funds",
                      "export_data", "modify_permissions", "publish"}


def evaluate_policy(content: str, metadata: dict[str, Any], draft: dict) -> list[str]:
    """Conservatively route sensitive work to a reviewer. Never make decisions by LLM alone."""
    reasons = []
    if metadata.get("sensitive") is True or metadata.get("requires_approval") is True:
        reasons.append("explicit_sensitive_flag")
    if str(metadata.get("requested_action", "")).lower() in CONTROLLED_ACTIONS:
        reasons.append("controlled_external_action")
    amount = metadata.get("amount")
    try:
        if amount is not None and float(amount) >= 1000:
            reasons.append("large_amount")
    except (TypeError, ValueError, OverflowError):
        reasons.append("unvalidated_amount")
    if SENSITIVE_PATTERN.search(content):
        reasons.append("sensitive_content")
    if draft.get("priority") == "high":
        reasons.append("high_priority_review")
    return list(dict.fromkeys(reasons))


def route_kind(kind: str) -> str:
    routes = {"email": "email_agent", "document": "document_agent",
              "request": "request_agent", "report": "report_agent"}
    if kind not in routes:
        raise ValueError(f"Unsupported workflow type: {kind}")
    return routes[kind]

"""Offline regression tests for source-grounded email drafting.

These tests exercise NastaraOps deterministic evidence guard, not Qwen quality,
embedding similarity, or the end-to-end Docker/Worker pipeline.
"""

from pathlib import Path

import pytest

from app.evaluation import evaluate_structural
from app.llm import apply_grounding_guard, mock_analyze

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_DOCUMENT = (
    PROJECT_ROOT / "examples" / "knowledge_base" / "premium-wireless-headphones-demo.md"
)
SOURCE_ID = "test-source-headphones"


@pytest.fixture
def demo_knowledge():
    return [
        {
            "source_id": SOURCE_ID,
            "document_id": "test-demo-headphones",
            "title": "Demo Headphones — Product & Returns Policy",
            "filename": DEMO_DOCUMENT.name,
            "page": None,
            "score": 0.75,
            "text": DEMO_DOCUMENT.read_text(encoding="utf-8"),
        }
    ]


def analyze_offline(content, knowledge):
    draft = mock_analyze("email", content, [])
    return apply_grounding_guard("email", draft, knowledge, content)


def test_supported_return_window_cites_source(demo_knowledge):
    draft = analyze_offline(
        "What is the return or replacement window for defective headphones?",
        demo_knowledge,
    )
    assert draft["grounded"] is True
    assert draft["citations"] == [SOURCE_ID]
    assert draft["unsupported_questions"] == []
    assert "30 calendar days" in draft["reply_draft"]
    assert "demo/synthetic data" in draft["reply_draft"]
    assert draft["retrieved_sources"][0]["demo_source"] is True


@pytest.mark.parametrize(
    "question, expected_topic",
    [
        ("What is the exact retail price in USD?", "Exact retail price"),
        ("What is the exact warranty duration in months?", "Warranty duration"),
    ],
)
def test_unavailable_facts_abstain(demo_knowledge, question, expected_topic):
    draft = analyze_offline(question, demo_knowledge)
    assert draft["grounded"] is False
    assert draft["citations"] == []
    assert expected_topic in draft["unsupported_questions"]
    assert "not available" in draft["reply_draft"].lower()
    assert draft["confidence"] <= 0.35


def test_mixed_evidence_cites_only_supported_topic(demo_knowledge):
    content = (
        "Hello, I purchased the Premium Wireless Headphones (DEMO).\n"
        "1. How many calendar days do I have to request a return or replacement "
        "if the headphones are defective?\n"
        "2. What is the exact warranty duration in months?\n"
        "Use only the knowledge base; do not invent facts."
    )
    draft = analyze_offline(content, demo_knowledge)
    assert draft["grounded"] is False  # One answer is still unsupported.
    assert draft["citations"] == [SOURCE_ID]  # Supported return-policy claim.
    assert draft["unsupported_questions"] == ["Warranty duration"]
    assert "30 calendar days" in draft["reply_draft"]
    assert "not available" in draft["reply_draft"].lower()

    evaluation = evaluate_structural(content=content, draft=draft)
    assert evaluation["details"]["evidence_status"] == "partially_supported"
    assert evaluation["metrics"]["citation_id_integrity"] == 1.0
    assert evaluation["metrics"]["abstention_language_present"] == 1.0
    assert evaluation["details"]["invalid_citation_ids"] == []


def test_no_source_does_not_invent_citations():
    draft = analyze_offline("What is the return policy for defective products?", [])
    assert draft["grounded"] is False
    assert draft["citations"] == []
    assert draft["unsupported_questions"]
    assert "not available" in draft["reply_draft"].lower()


def test_irrelevant_evidence_is_not_a_citation(demo_knowledge):
    # Even though the retrieved demo text is about the right product, it does
    # not contain a price or warranty duration.
    draft = analyze_offline("What is the exact warranty duration?", demo_knowledge)
    assert draft["retrieved_sources"]
    assert draft["citations"] == []
    assert draft["sources"] == []


def test_non_email_agent_rejects_invented_source_id(demo_knowledge):
    draft = mock_analyze("request", "Summarize the support request.", [])
    draft["citations"] = ["invented-source-id"]
    draft["confidence"] = 0.95
    guarded = apply_grounding_guard("request", draft, demo_knowledge)
    assert guarded["citations"] == []
    assert guarded["grounded"] is False
    assert guarded["confidence"] <= 0.4


def test_structural_evaluator_flags_invalid_citation():
    draft = {
        "retrieved_sources": [{"source_id": SOURCE_ID}],
        "citations": ["not-in-retrieval"],
        "unsupported_questions": [],
        "extracted_fields": {},
        "reply_draft": "Draft",
    }
    result = evaluate_structural(content="Test", draft=draft)
    assert result["metrics"]["citation_id_integrity"] == 0.0
    assert result["details"]["invalid_citation_ids"] == ["not-in-retrieval"]
    assert result["metrics"]["faithfulness_judge"] is None

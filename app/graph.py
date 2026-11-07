"""Four specialists connected by a durable LangGraph HITL workflow."""

import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.db import SessionLocal
from app.config import get_settings
from app.llm import analyze
from app.mcp_client import lookup_policy
from app.metrics import collect_metrics
from app.policy import evaluate_policy, route_kind
from app.rag import search_knowledge


class WorkflowState(TypedDict, total=False):
    task_id: str
    kind: str
    title: str
    content: str
    metadata: dict[str, Any]
    memory_context: list[str]
    knowledge_context: list[dict[str, Any]]
    policy_hint: str
    agent: str
    execution_trace: list[str]
    draft: dict
    risk_reasons: list[str]
    needs_approval: bool
    approved: bool
    reviewer: str
    decision_note: str
    final_output: dict
    final_status: str


async def gather_context(state: WorkflowState) -> dict:
    # Do not inject summaries from unrelated tasks into the current model prompt.
    # MemoryEntry remains persisted for the UI/audit trail, but cross-task retrieval
    # is disabled until memory has an explicit tenant/customer/conversation scope.
    # This prevents one customer's email details from contaminating another draft.
    policy = await lookup_policy(state["kind"])
    return {"memory_context": [], "policy_hint": policy,
            "execution_trace": ["context"]}


async def retrieve_knowledge(state: WorkflowState) -> dict:
    """Retrieve only approved company evidence before specialist-agent execution."""
    settings = get_settings()
    knowledge = []
    if settings.llm_mode == "ollama":
        query = (state.get("title", "") + "\n" + state["content"][:6000]).strip()
        knowledge = await search_knowledge(query)
    return {"knowledge_context": knowledge,
            "execution_trace": state.get("execution_trace", []) + ["rag_retrieval"]}


def assign_agent(state: WorkflowState) -> dict:
    return {"agent": route_kind(state["kind"]),
            "execution_trace": state.get("execution_trace", []) + ["router"]}


async def specialist(state: WorkflowState, kind: str) -> dict:
    content = state["content"]
    if kind == "report":
        async with SessionLocal() as session:
            facts = await collect_metrics(session)
        content = "Report request: " + content + "\nDATABASE_KPIS: " + json.dumps(facts)
    draft = await analyze(kind, content, state.get("memory_context", []),
                          state.get("policy_hint", ""), state.get("knowledge_context", []))
    if kind == "report":
        draft["verified_metrics"] = facts
    return {"draft": draft, "execution_trace": state.get("execution_trace", []) + [kind + "_agent"]}


async def email_agent(state: WorkflowState) -> dict:
    return await specialist(state, "email")


async def document_agent(state: WorkflowState) -> dict:
    return await specialist(state, "document")


async def request_agent(state: WorkflowState) -> dict:
    return await specialist(state, "request")


async def report_agent(state: WorkflowState) -> dict:
    return await specialist(state, "report")


def policy_gate(state: WorkflowState) -> dict:
    reasons = evaluate_policy(state["content"], state.get("metadata", {}), state["draft"])
    return {"risk_reasons": reasons, "needs_approval": bool(reasons),
            "execution_trace": state.get("execution_trace", []) + ["policy_gate"]}


def approval_required(state: WorkflowState) -> str:
    return "review" if state["needs_approval"] else "finalize"


def human_review(state: WorkflowState) -> dict:
    # This node has no side effects before interrupt: safe to re-run on resume.
    decision = interrupt({"task_id": state["task_id"], "risk_reasons": state["risk_reasons"],
                          "proposed_draft": state["draft"], "question": "Approve this draft?"})
    return {"approved": bool(decision["approved"]), "reviewer": decision["reviewer"],
            "decision_note": decision.get("note", ""),
            "execution_trace": state.get("execution_trace", []) + ["human_review"]}


def finalize(state: WorkflowState) -> dict:
    approved = not state["needs_approval"] or state.get("approved") is True
    return {"execution_trace": state.get("execution_trace", []) + ["finalize"],
            "final_status": "completed" if approved else "rejected",
            "final_output": {"agent": state["agent"], "draft": state["draft"],
                             "approval_required": state["needs_approval"],
                             "approved": approved,
                             "reviewer": state.get("reviewer"),
                             "decision_note": state.get("decision_note", ""),
                             "note": "Draft and analysis only. No external action has been executed."}}


def build_graph(checkpointer):
    builder = StateGraph(WorkflowState)
    builder.add_node("context", gather_context)
    builder.add_node("rag_retrieval", retrieve_knowledge)
    builder.add_node("router", assign_agent)
    for name, handler in (("email_agent", email_agent), ("document_agent", document_agent),
                          ("request_agent", request_agent), ("report_agent", report_agent)):
        builder.add_node(name, handler)
        builder.add_edge(name, "policy_gate")
    builder.add_node("policy_gate", policy_gate)
    builder.add_node("human_review", human_review)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "context")
    builder.add_edge("context", "rag_retrieval")
    builder.add_edge("rag_retrieval", "router")
    builder.add_conditional_edges("router", lambda state: route_kind(state["kind"]),
                                  {kind: kind for kind in ("email_agent", "document_agent",
                                                           "request_agent", "report_agent")})
    builder.add_conditional_edges("policy_gate", approval_required,
                                  {"review": "human_review", "finalize": "finalize"})
    builder.add_edge("human_review", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)

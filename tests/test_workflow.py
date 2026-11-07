"""LangGraph approval/deny behavior, isolated with in-memory checkpoints.

No Ollama inference, Redis, MCP, Qdrant, PostgreSQL, or live API is called.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app import graph as workflow
from app.llm import mock_analyze


@pytest.fixture
def isolated_graph(monkeypatch):
    async def sample_policy(_kind):
        return 'Use draft-only mode. No external action.'

    async def sample_analysis(kind, content, memory, policy_hint, knowledge):
        return mock_analyze(kind, content, memory)

    monkeypatch.setattr(workflow, 'lookup_policy', sample_policy)
    monkeypatch.setattr(workflow, 'analyze', sample_analysis)
    monkeypatch.setattr(workflow, 'get_settings', lambda: SimpleNamespace(llm_mode='mock'))
    return workflow.build_graph(InMemorySaver())


def _input(*, sensitive=False):
    return {
        'task_id': str(uuid4()), 'kind': 'request', 'title': 'Workflow test',
        'content': 'A customer asks for an order status update.' if not sensitive else
                   'A customer requests a refund for a delayed shipment.',
        'metadata': {} if not sensitive else {'requested_action': 'issue_refund', 'requires_approval': True},
    }


def test_normal_request_completes_without_approval(isolated_graph):
    async def run():
        state = _input()
        config = {'configurable': {'thread_id': state['task_id']}}
        await isolated_graph.ainvoke(state, config)
        return await isolated_graph.aget_state(config)

    snapshot = asyncio.run(run())
    assert snapshot.next == ()
    assert snapshot.values['final_status'] == 'completed'
    assert snapshot.values['final_output']['approval_required'] is False
    assert snapshot.values['final_output']['approved'] is True


@pytest.mark.parametrize('decision,expected', [(False, 'rejected'), (True, 'completed')])
def test_sensitive_workflow_waits_then_resumes(isolated_graph, decision, expected):
    async def run():
        state = _input(sensitive=True)
        config = {'configurable': {'thread_id': state['task_id']}}
        await isolated_graph.ainvoke(state, config)
        paused = await isolated_graph.aget_state(config)
        assert 'human_review' in paused.next
        assert paused.values['needs_approval'] is True
        assert not paused.values.get('final_status')
        await isolated_graph.ainvoke(Command(resume={
            'approved': decision, 'reviewer': 'Test Reviewer', 'note': 'Offline test only',
        }), config)
        return await isolated_graph.aget_state(config)

    snapshot = asyncio.run(run())
    assert snapshot.next == ()
    assert snapshot.values['final_status'] == expected
    output = snapshot.values['final_output']
    assert output['approval_required'] is True
    assert output['approved'] is decision
    assert output['reviewer'] == 'Test Reviewer'
    assert 'No external action' in output['note']

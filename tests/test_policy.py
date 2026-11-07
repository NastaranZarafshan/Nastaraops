"""Offline authorization checks; no databases, APIs, or external actions."""

import pytest

from app.policy import evaluate_policy, route_kind


@pytest.mark.parametrize(
    ('kind', 'agent'),
    [('email', 'email_agent'), ('request', 'request_agent'),
     ('document', 'document_agent'), ('report', 'report_agent')],
)
def test_specialist_routing(kind, agent):
    assert route_kind(kind) == agent


def test_unknown_workflow_kind_rejected():
    with pytest.raises(ValueError, match='Unsupported workflow type'):
        route_kind('wire_transfer')


def test_regular_request_does_not_require_approval():
    assert evaluate_policy('Please summarize this shipping delay.', {}, {'priority': 'medium'}) == []


def test_explicit_approval_flag_is_enforced():
    reasons = evaluate_policy('Prepare a draft response.', {'requires_approval': True}, {'priority': 'low'})
    assert 'explicit_sensitive_flag' in reasons


def test_refund_action_requires_human_review():
    reasons = evaluate_policy('Please issue a refund.', {'requested_action': 'issue_refund'}, {'priority': 'medium'})
    assert 'controlled_external_action' in reasons
    assert 'sensitive_content' in reasons


def test_high_value_requires_human_review():
    reasons = evaluate_policy('Check this transaction.', {'amount': 1500}, {'priority': 'low'})
    assert 'large_amount' in reasons


def test_invalid_amount_is_conservatively_flagged():
    reasons = evaluate_policy('Review amount.', {'amount': 'not-a-number'}, {'priority': 'low'})
    assert 'unvalidated_amount' in reasons


def test_high_priority_requires_review():
    reasons = evaluate_policy('Service request.', {}, {'priority': 'high'})
    assert 'high_priority_review' in reasons

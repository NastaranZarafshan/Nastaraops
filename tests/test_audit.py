"""Hash-chain primitives; pure and isolated from real PostgreSQL audit history."""

from app.audit import GENESIS, hash_event


def _hash(previous, **overrides):
    values = dict(task_id='sample-task', actor='test-reviewer', action='approved',
                  detail={'note': 'Sample only'}, created_at='2026-11-07T00:00:00+00:00')
    values.update(overrides)
    return hash_event(previous, **values)


def test_genesis_has_expected_length():
    assert GENESIS == '0' * 64


def test_audit_hash_is_repeatable_and_well_formed():
    result = _hash(GENESIS)
    assert result == _hash(GENESIS)
    assert len(result) == 64
    assert all(c in '0123456789abcdef' for c in result)


def test_hash_changes_when_decision_or_actor_changes():
    first = _hash(GENESIS)
    assert _hash(GENESIS, action='denied') != first
    assert _hash(GENESIS, actor='another-reviewer') != first


def test_hash_changes_when_previous_link_changes():
    first = _hash(GENESIS)
    second = _hash(first)
    assert second != first
    assert _hash('f' * 64) != first


def test_detail_dict_key_order_does_not_change_hash():
    left = _hash(GENESIS, detail={'approved': True, 'note': 'test'})
    right = _hash(GENESIS, detail={'note': 'test', 'approved': True})
    assert left == right

"""API contract tests with startup disabled and no live service connections.

These exercise the real FastAPI app routes, dependencies, and validation,
but intentionally do not start the DB-backed lifespan or mutate application data.
"""

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from app.main import app

    @asynccontextmanager
    async def no_external_startup(_app):
        yield

    # Normal API startup connects to PostgreSQL; avoid it in isolated tests.
    monkeypatch.setattr(app.router, 'lifespan_context', no_external_startup)
    with TestClient(app) as test_client:
        yield test_client


def _auth():
    from app.config import get_settings
    return {'x-api-key': get_settings().api_key}


def test_health_is_public_and_reports_mode(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'
    assert response.json()['llm_mode'] in {'mock', 'ollama'}


def test_dashboard_is_served(client):
    response = client.get('/')
    assert response.status_code == 200
    assert 'NastaraOps' in response.text


def test_static_asset_allowlist_rejects_unknown_file(client):
    response = client.get('/assets/nonexistent-file')
    assert response.status_code == 404


@pytest.mark.parametrize('route', ['/api/tasks', '/api/audit', '/api/audit/verify'])
def test_private_routes_reject_missing_key(client, route):
    assert client.get(route).status_code == 401


def test_private_routes_reject_wrong_key(client):
    response = client.get('/api/tasks', headers={'x-api-key': 'definitely-not-a-valid-key'})
    assert response.status_code == 401


def test_create_workflow_requires_api_key(client):
    response = client.post('/api/tasks', json={'kind': 'request', 'title': 'Sample request',
                                               'content': 'Do not execute.'})
    assert response.status_code == 401


def test_create_workflow_rejects_bad_type_before_database_access(client):
    response = client.post('/api/tasks', headers=_auth(),
                           json={'kind': 'invalid-type', 'title': 'Sample request',
                                 'content': 'Do not execute.'})
    assert response.status_code == 422


def test_create_workflow_rejects_empty_content_before_database_access(client):
    response = client.post('/api/tasks', headers=_auth(),
                           json={'kind': 'request', 'title': 'Sample request', 'content': ''})
    assert response.status_code == 422


def test_decision_requires_valid_reviewer_before_database_access(client):
    response = client.post('/api/tasks/sample-task/decision', headers=_auth(),
                           json={'approved': False, 'reviewer': 'X', 'note': 'test'})
    assert response.status_code == 422

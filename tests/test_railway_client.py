from unittest.mock import patch
import pytest
from src.railway_client import (
    verify_railway_token,
    ensure_railway_project,
    set_worker_start_command,
    redeploy_worker,
    stop_worker,
    get_latest_deployment
)

def test_verify_railway_token_success():
    mock_resp = {"data": {"me": {"email": "test@example.com", "name": "Tester"}}}
    with patch("src.railway_client.execute_graphql", return_value=mock_resp):
        res = verify_railway_token("valid_token")
        assert res is not None
        assert res["email"] == "test@example.com"

def test_verify_railway_token_failure():
    mock_resp = {"errors": [{"message": "Not Authorized"}]}
    with patch("src.railway_client.execute_graphql", return_value=mock_resp):
        res = verify_railway_token("bad_token")
        assert res is None

def test_ensure_railway_project_found():
    mock_projects = {
        "data": {
            "projects": {
                "edges": [
                    {
                        "node": {
                            "id": "proj-123",
                            "name": "file-streamer",
                            "environments": {
                                "edges": [{"node": {"id": "env-123", "name": "production"}}]
                            },
                            "services": {
                                "edges": [
                                    {"node": {"id": "srv-uploader", "name": "uploader"}},
                                    {"node": {"node": "srv-streamer", "name": "streamer"}}
                                ]
                            }
                        }
                    }
                ]
            }
        }
    }
    with patch("src.railway_client.execute_graphql", return_value=mock_projects):
        p_id, e_id, s_id = ensure_railway_project("test_token", project_name="file-streamer")
        assert p_id == "proj-123"
        assert e_id == "env-123"
        assert s_id == "srv-uploader"

def test_set_worker_start_command():
    mock_resp = {"data": {"serviceInstanceUpdate": True}}
    with patch("src.railway_client.execute_graphql", return_value=mock_resp):
        ok = set_worker_start_command("tok", "env-123", "srv-123", "python worker.py")
        assert ok is True

def test_redeploy_worker():
    mock_resp = {"data": {"serviceInstanceRedeploy": True}}
    with patch("src.railway_client.execute_graphql", return_value=mock_resp):
        ok = redeploy_worker("tok", "env-123", "srv-123")
        assert ok is True

def test_stop_worker():
    mock_resp = {"data": {"deploymentRemove": True}}
    with patch("src.railway_client.execute_graphql", return_value=mock_resp):
        ok = stop_worker("tok", "dep-123")
        assert ok is True

def test_get_latest_deployment():
    mock_resp = {
        "data": {
            "service": {
                "deployments": {
                    "edges": [
                        {"node": {"id": "dep-active", "status": "SUCCESS", "createdAt": "2026-10-21"}}
                    ]
                }
            }
        }
    }
    with patch("src.railway_client.execute_graphql", return_value=mock_resp):
        dep = get_latest_deployment("tok", "srv-123", "env-123")
        assert dep is not None
        assert dep["id"] == "dep-active"
        assert dep["status"] == "SUCCESS"

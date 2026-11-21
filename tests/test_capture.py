import asyncio
import json

from gwsc_local_termux import config
from gwsc_local_termux.capture import (
    _read_request_body,
    redact_body,
    redact_mapping,
)
from gwsc_local_termux.logging_utils import redact_text


def test_redact_mapping_removes_credentials():
    payload = {
        "email": "user@example.invalid",
        "password": "super-secret",
        "nested": {"token": "gwsc_abcdefghijklmnop", "safe": 1},
        "list": [{"scAuthToken": "abc"}],
    }
    redacted = redact_mapping(payload)
    assert redacted["email"] == "user@example.invalid"
    assert redacted["password"] == "***"
    assert redacted["nested"]["token"] == "***"
    assert redacted["nested"]["safe"] == 1
    assert redacted["list"][0]["scAuthToken"] == "***"


def test_redact_body_json_and_form():
    raw = json.dumps({"password": "hunter2", "ticket": "ticket-value"}).encode()
    redacted = redact_body(raw, "application/json", 4096)
    assert redacted["json"]["password"] == "***"
    assert redacted["json"]["ticket"] == "***"

    form = redact_body(b"scAuthToken=abc&state=in_game", "application/x-www-form-urlencoded", 4096)
    assert form["form"]["scAuthToken"] == "***"
    assert form["form"]["state"] == "in_game"


def test_redact_text_hides_tokens():
    assert "***" in redact_text("Authorization: Bearer abcdefghijklmnop")
    assert "***" in redact_text("token=gwsc_abcdefghijklmnopqrstuvwxyz")


def test_capture_writes_redacted_request_and_response(client, tmp_path, monkeypatch):
    capture_dir = tmp_path / "captures"
    capture_file = capture_dir / "captures.jsonl"
    monkeypatch.setattr(config, "CAPTURE_ENABLED", True)
    monkeypatch.setattr(config, "CAPTURE_DIR", capture_dir)
    monkeypatch.setattr(config, "CAPTURE_FILE", capture_file)

    response = client.post(
        "/gwsc/api/register",
        json={
            "email": "capture@example.invalid",
            "nickname": "capture",
            "password": "capture-password-123",
        },
    )
    assert response.status_code == 200
    raw_token = response.json()["token"]

    content = capture_file.read_text(encoding="utf-8")
    assert "capture-password-123" not in content
    assert raw_token not in content
    assert '"***"' in content
    entry = json.loads(content.splitlines()[-1])
    assert entry["path"] == "/gwsc/api/register"
    assert entry["request_body"]["json"]["password"] == "***"
    assert entry["response_body"]["json"]["token"] == "***"


def test_body_limit_rejects_oversized_request(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_BODY_BYTES", 32)
    response = client.post(
        "/gwsc/api/login",
        content=json.dumps({"email": "a" * 200, "password": "x" * 200}),
        headers={
            "content-type": "application/json",
            "origin": "https://example.invalid",
        },
    )
    assert response.status_code == 413
    assert response.json()["error"] == "request_too_large"
    # RequestID and CORS must wrap BodyLimit so rejected requests are still
    # traceable and usable from a browser context.
    assert response.headers.get("x-request-id")
    assert response.headers.get("access-control-allow-origin") in (
        "*",
        "https://example.invalid",
    )


def test_read_request_body_keeps_full_body_and_reports_excess():
    def receive_for(messages):
        async def receive():
            return messages.pop(0)

        return receive

    messages = [
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"y" * 60, "more_body": False},
    ]
    body, exceeded = asyncio.run(_read_request_body(receive_for(messages), 200))
    assert body == b"x" * 60 + b"y" * 60
    assert exceeded is False

    messages = [
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"y" * 60, "more_body": False},
    ]
    body, exceeded = asyncio.run(_read_request_body(receive_for(messages), 100))
    assert body == b"x" * 60 + b"y" * 40
    assert exceeded is True


def test_capture_does_not_truncate_the_forwarded_body(client, tmp_path, monkeypatch):
    capture_dir = tmp_path / "captures"
    capture_file = capture_dir / "captures.jsonl"
    monkeypatch.setattr(config, "CAPTURE_ENABLED", True)
    monkeypatch.setattr(config, "CAPTURE_DIR", capture_dir)
    monkeypatch.setattr(config, "CAPTURE_FILE", capture_file)
    monkeypatch.setattr(config, "CAPTURE_MAX_BODY", 16)

    response = client.post(
        "/gwsc/api/register",
        json={
            "email": "longbody@example.invalid",
            "nickname": "longbody",
            "password": "p" * 64,
        },
    )
    # The stored capture is truncated, but the route must still receive the
    # complete body and succeed.
    assert response.status_code == 200, response.text
    entry = json.loads(capture_file.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["request_body_truncated"] is True


def test_error_shape_is_stable(client):
    response = client.get("/gwsc/api/me")
    assert response.status_code == 401
    payload = response.json()
    assert payload["error"] == "unauthorized"
    assert payload["message"]
    assert payload["detail"]["error"] == "unauthorized"

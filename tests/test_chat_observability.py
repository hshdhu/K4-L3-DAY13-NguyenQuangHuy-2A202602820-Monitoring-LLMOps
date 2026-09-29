from __future__ import annotations

import json
import asyncio
import re
from pathlib import Path

import httpx

from app import logging_config
from app.main import app
from app.pii import hash_user_id


def test_chat_response_log_exposes_quality_for_dashboard(
    monkeypatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)

    async def send_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.post(
                "/chat",
                json={
                    "user_id": "student-01",
                    "session_id": "session-01",
                    "feature": "qa",
                    "message": "Explain observability",
                },
            )

    response = asyncio.run(send_request())

    assert response.status_code == 200
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    response_event = next(event for event in events if event["event"] == "response_sent")
    assert response_event["quality_score"] == response.json()["quality_score"]
    assert response_event["ttft_ms"] == response.json()["ttft_ms"]
    assert response_event["tool_name"] == "retrieval"
    assert response_event["tool_success"] is True


def test_request_ids_and_context_are_isolated(monkeypatch, tmp_path):
    from structlog.contextvars import bind_contextvars, clear_contextvars

    path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", path)
    monkeypatch.setenv("APP_ENV", "test")

    async def run_requests():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async def send(index):
                bind_contextvars(stale_marker="must-not-leak")
                return await client.post("/chat", headers={"x-request-id": "req-abcdef12"} if index == 0 else {},
                    json={"user_id": f"student-{index}", "session_id": f"session-{index}",
                          "feature": f"feature-{index}", "message": "Explain monitoring"})
            try:
                return await asyncio.gather(*(send(index) for index in range(3)))
            finally:
                clear_contextvars()

    responses = asyncio.run(run_requests())
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    ids = []
    for index, response in enumerate(responses):
        assert response.status_code == 200
        cid = response.headers["x-request-id"]
        ids.append(cid)
        assert re.fullmatch(r"req-[0-9a-f]{8}", cid)
        assert response.json()["correlation_id"] == cid
        assert float(response.headers["x-response-time-ms"]) >= 0
        matching = [event for event in events if event.get("correlation_id") == cid]
        assert {event["event"] for event in matching} == {"request_received", "response_sent"}
        for event in matching:
            assert event["user_id_hash"] == hash_user_id(f"student-{index}")
            assert event["session_id"] == f"session-{index}"
            assert event["feature"] == f"feature-{index}"
            assert event["env"] == "test"
            assert event["model"]
            assert "stale_marker" not in event
    assert ids[0] == "req-abcdef12"
    assert len(set(ids)) == 3

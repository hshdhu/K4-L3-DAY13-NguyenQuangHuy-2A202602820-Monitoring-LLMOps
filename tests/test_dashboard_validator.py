from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_validator(config_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "validate_dashboard.py"),
            "--config",
            str(config_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_repository_dashboard_contract_is_valid() -> None:
    result = run_validator(REPO_ROOT / "config" / "dashboard.yaml")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "6/6 panel" in result.stdout


def test_validator_rejects_panel_without_threshold(tmp_path: Path) -> None:
    payload = yaml.safe_load(
        (REPO_ROOT / "config" / "dashboard.yaml").read_text(encoding="utf-8")
    )
    del payload["dashboard"]["panels"][0]["threshold"]
    invalid_config = tmp_path / "dashboard.yaml"
    invalid_config.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    result = run_validator(invalid_config)

    assert result.returncode == 1
    assert "latency.threshold" in result.stdout


def test_validator_rejects_panel_without_query_example(tmp_path: Path) -> None:
    payload = yaml.safe_load(
        (REPO_ROOT / "config" / "dashboard.yaml").read_text(encoding="utf-8")
    )
    payload["dashboard"]["panels"][0].pop("query", None)
    invalid_config = tmp_path / "dashboard.yaml"
    invalid_config.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    result = run_validator(invalid_config)

    assert result.returncode == 1
    assert "latency.query" in result.stdout


def test_runtime_aggregates_failures_and_successful_retrieval():
    from app.dashboard import aggregate
    records = [
        {"event": "request_received"}, {"event": "request_received"},
        {"event": "response_sent", "latency_ms": 120, "ttft_ms": 50,
         "tokens_in": 20, "tokens_out": 80, "cost_usd": 0.00126,
         "quality_score": 0.8, "tool_name": "retrieval", "tool_success": True},
        {"event": "request_failed", "error_type": "RuntimeError",
         "tool_name": "retrieval", "tool_success": False},
    ]
    result = aggregate(records)
    assert result["traffic"]["count"] == 2
    assert result["errors"] == {"error_rate_pct": 50, "tool_success_rate_pct": 50,
                                "breakdown": {"RuntimeError": 1}}
    assert result["latency"]["p95"] == 120
    assert result["latency"]["ttft_p95"] == 50
    assert result["cost"]["total"] == 0.00126
    assert result["tokens"] == {"input": 20, "output": 80}
    assert result["quality"]["mean"] == 0.8
    assert aggregate([])["errors"]["error_rate_pct"] is None


def test_runtime_window_and_six_panels(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone
    from app import dashboard, logging_config
    now = datetime.now(timezone.utc)
    path = tmp_path / "logs.jsonl"
    path.write_text("\n".join([
        json.dumps({"ts": now.isoformat(), "event": "request_received"}),
        json.dumps({"ts": (now - timedelta(hours=2)).isoformat(), "event": "request_received"}),
        '{"incomplete":',
    ]), encoding="utf-8")
    records, skipped = dashboard.read_window(path, now - timedelta(hours=1), now)
    assert len(records) == 1
    assert skipped == 1
    monkeypatch.setattr(logging_config, "LOG_PATH", path)
    page = dashboard.render_dashboard()
    assert page.count("<section>") == 6
    assert 'content="30"' in page
    assert "count: 1" in page

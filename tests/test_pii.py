from app.pii import scrub_text
import pytest


def test_scrub_email() -> None:
    out = scrub_text("Email me at student@vinuni.edu.vn")
    assert "student@" not in out
    assert "REDACTED_EMAIL" in out


def test_scrub_common_vietnamese_phone_formats() -> None:
    phone_numbers = (
        "0901234567",
        "090 123 4567",
        "090.123.4567",
        "090-123-4567",
        "+84 90 123 4567",
    )

    for phone_number in phone_numbers:
        out = scrub_text(f"Contact: {phone_number}")
        assert phone_number not in out
        assert "REDACTED_PHONE_VN" in out


@pytest.mark.parametrize("value,kind", [
    ("student+lab@example.com", "EMAIL"),
    ("012345678901", "CCCD"),
    ("4111111111111111", "CREDIT_CARD"),
    ("4111 1111 1111 1111", "CREDIT_CARD"),
    ("4111-1111-1111-1111", "CREDIT_CARD"),
    ("0901 2345 6789 1234", "CREDIT_CARD"),
])
def test_required_pii_formats(value, kind):
    assert scrub_text(f"Contact: {value}") == f"Contact: [REDACTED_{kind}]"


def test_scrub_preserves_normal_text():
    assert scrub_text("Request req-abcdef12 took 150 ms") == "Request req-abcdef12 took 150 ms"


def test_adjacent_pii_values_are_redacted_separately():
    value = "student@example.com 0901234567 012345678901 4111 1111 1111 1111"
    assert scrub_text(value) == (
        "[REDACTED_EMAIL] [REDACTED_PHONE_VN] [REDACTED_CCCD] [REDACTED_CREDIT_CARD]"
    )


def test_logging_scrubs_nested_values_before_writing(monkeypatch, tmp_path, capsys):
    import json
    import structlog
    from structlog.contextvars import clear_contextvars
    from app import logging_config

    clear_contextvars()
    path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", path)
    logging_config.configure_logging()
    logger = structlog.wrap_logger(
        structlog.PrintLogger(), processors=structlog.get_config()["processors"]
    )
    logger.info("contact student+lab@example.com", detail="012345678901",
                payload={"items": ["0901234567", {"card": "4111 1111 1111 1111"}], "count": 2})
    file_text = path.read_text(encoding="utf-8")
    terminal_text = capsys.readouterr().out
    for output in (file_text, terminal_text):
        for raw in ("student+lab@example.com", "012345678901", "0901234567", "4111 1111 1111 1111"):
            assert raw not in output
        for kind in ("EMAIL", "CCCD", "PHONE_VN", "CREDIT_CARD"):
            assert f"[REDACTED_{kind}]" in output
    assert json.loads(file_text)["payload"]["count"] == 2

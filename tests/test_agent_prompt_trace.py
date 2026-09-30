from __future__ import annotations

from contextlib import contextmanager

from app import agent as agent_module


class ManagedPrompt:
    version = 3

    def compile(self, **variables: str) -> str:
        return (
            f"Feature={variables['feature']}\n"
            f"Docs={variables['docs']}\n"
            f"Question={variables['message']}"
        )


class RecordingLangfuseClient:
    def __init__(self) -> None:
        self.prompt = ManagedPrompt()
        self.span_updates: list[dict] = []

    def get_prompt(self, name: str, **kwargs):
        return self.prompt

    def update_current_span(self, **kwargs) -> None:
        self.span_updates.append(kwargs)


def test_agent_records_prompt_version_with_v4_observation_api(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_PROMPT_NAME", "day13-chat")
    monkeypatch.setenv("LANGFUSE_PROMPT_LABEL", "production")
    client = RecordingLangfuseClient()
    monkeypatch.setattr(agent_module, "get_langfuse_client", lambda: client)
    monkeypatch.setattr(agent_module, "tracing_enabled", lambda: True)

    propagated: list[dict] = []

    @contextmanager
    def record_attributes(**kwargs):
        propagated.append(kwargs)
        yield

    monkeypatch.setattr(agent_module, "propagate_attributes", record_attributes)

    agent = agent_module.LabAgent()
    agent_module.LabAgent.run.__wrapped__(
        agent,
        user_id="student-01",
        feature="qa",
        session_id="session-01",
        message="Explain traces",
        correlation_id="req-12345678",
    )

    span_update = client.span_updates[-1]
    assert span_update["metadata"] == {
        "doc_count": 1,
        "query_preview": "Explain traces",
        "prompt_name": "day13-chat",
        "prompt_label": "production",
        "prompt_version": "3",
        "prompt_source": "langfuse",
        "prompt_fetch_error": "",
    }
    assert span_update["version"] == "3"
    assert propagated[0]["metadata"]["correlation_id"] == "req-12345678"
    assert propagated[-1]["prompt"] is client.prompt


def test_generation_records_sanitized_io_usage_and_cost(monkeypatch):
    from app import mock_llm
    updates = []

    class Client:
        def update_current_generation(self, **kwargs):
            updates.append(kwargs)

    monkeypatch.setattr(mock_llm, "get_langfuse_client", lambda: Client())
    response = mock_llm.FakeLLM().generate("Email student@example.com")
    assert "student@example.com" not in str(updates)
    assert "[REDACTED_EMAIL]" in updates[0]["input"]
    assert updates[0]["model"] == response.model
    assert updates[-1]["usage_details"] == {"input": response.usage.input_tokens, "output": response.usage.output_tokens}
    assert updates[-1]["cost_details"]["total"] == agent_module.LabAgent()._estimate_cost(
        response.usage.input_tokens, response.usage.output_tokens)


def test_observations_have_correct_parents_and_no_raw_capture(monkeypatch):
    import importlib
    from app import mock_llm
    observations, active = [], []

    class Observation:
        def __init__(self, fields):
            self.fields = fields
        def update(self, **kwargs):
            self.fields.update(kwargs)
        def end(self):
            pass

    class Client:
        @contextmanager
        def start_as_current_observation(self, **kwargs):
            fields = {**kwargs, "parent": active[-1].fields["name"] if active else None}
            obs = Observation(fields)
            observations.append(fields)
            active.append(obs)
            try:
                yield obs
            finally:
                active.pop()
        def update_current_span(self, **kwargs):
            active[-1].update(**kwargs)
        def update_current_generation(self, **kwargs):
            active[-1].update(**kwargs)

    client = Client()
    monkeypatch.setattr(importlib.import_module("langfuse._client.observe"), "get_client", lambda **kw: client)
    monkeypatch.setattr(agent_module, "get_langfuse_client", lambda: client)
    monkeypatch.setattr(mock_llm, "get_langfuse_client", lambda: client)
    monkeypatch.setattr(agent_module, "tracing_enabled", lambda: False)
    agent_module.LabAgent().run("student", "qa", "session", "student@example.com", "req-12345678")
    assert [(o["name"], o["as_type"], o["parent"]) for o in observations] == [
        ("lab-agent-run", "agent", None),
        ("retrieval", "retriever", "lab-agent-run"),
        ("fake-llm", "generation", "lab-agent-run"),
    ]
    assert "student@example.com" not in str(observations)
    assert observations[1]["input"] is None
    assert "output" not in observations[1]

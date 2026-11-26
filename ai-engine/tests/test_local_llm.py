"""Free local AI (Ollama) for the assistant, the CV rewrite and CV grading."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import local_llm
from app.services.assistant.service import AssistantUnavailableError, CareerAssistant
from app.services.assistant.settings import AssistantSettings
from app.services.cv_builder.parser import parse_cv_text
from app.services.cv_quality.labelling import LabelCv, ollama_grade
from app.services.cv_quality.rewriter import CvRewriter, CvRewriteSettings
from app.services.cv_quality.rubric import CRITERION_IDS
from cv_samples import STRONG_CV
from test_cv_quality import _improved_json


class FakeOllama:
    """Answers /api/chat like Ollama: queued messages, records payloads."""

    def __init__(self, *messages: dict, status: int = 200, text: str = "") -> None:
        self.messages = list(messages)
        self.calls: list[dict] = []
        self.status = status
        self.text = text

    def post(self, url: str, json: dict, timeout: float):  # noqa: A002 - httpx signature
        self.calls.append({"url": url, **json})
        message = self.messages.pop(0) if self.messages else {"role": "assistant", "content": ""}
        return SimpleNamespace(status_code=self.status, text=self.text, json=lambda: {"message": message},
                               raise_for_status=lambda: None)


def test_provider_switch(monkeypatch) -> None:
    assert local_llm.provider({}) == "anthropic"
    assert local_llm.provider({"AI_PROVIDER": "Ollama"}) == "ollama"
    settings = AssistantSettings.from_env({"ASSISTANT_ENABLED": "true", "AI_PROVIDER": "ollama"})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert settings.provider == "ollama" and settings.ready, "no key needed for the local model"
    assert CvRewriteSettings.from_env({"AI_PROVIDER": "ollama"}).has_credentials()
    assert local_llm.LocalLLMSettings.from_env({"OLLAMA_MODEL": "qwen2.5:3b"}).model == "qwen2.5:3b"


def test_local_assistant_calls_tools_then_answers() -> None:
    fake = FakeOllama(
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "search_jobs", "arguments": {"query": "data"}}}]},
        {"role": "assistant", "content": "Here are **2 jobs** for you."},
    )
    executor = SimpleNamespace(run=lambda name, args: (json.dumps({"jobs": [{"id": "J1"}], "args": args}), False))
    assistant = CareerAssistant(AssistantSettings(enabled=True, provider="ollama"), client=fake)
    reply = assistant.reply(history=[{"role": "assistant", "content": "Hi"}], message="Find data jobs", personal_context="- Name: Amira", executor=executor)
    assert reply.text == "Here are **2 jobs** for you." and reply.tools_used == ["search_jobs"]
    assert reply.model.startswith("ollama:")
    first, second = fake.calls
    assert first["messages"][0]["role"] == "system" and "Amira" in first["messages"][0]["content"]
    assert first["tools"][0]["function"]["name"] == "search_jobs"
    assert second["messages"][-1] == {"role": "tool", "content": json.dumps({"jobs": [{"id": "J1"}], "args": {"query": "data"}}), "tool_name": "search_jobs"}


def test_local_errors_are_clear() -> None:
    assistant = CareerAssistant(AssistantSettings(enabled=True, provider="ollama"), client=FakeOllama(status=404))
    with pytest.raises(AssistantUnavailableError, match="not installed"):
        assistant.reply(history=[], message="hi", personal_context=None, executor=SimpleNamespace(run=None))


def test_model_without_tools_answers_without_them() -> None:
    class NoTools(FakeOllama):
        def post(self, url, json, timeout):  # noqa: A002
            if "tools" in json:
                self.calls.append(json)
                return SimpleNamespace(status_code=400, text='{"error":"model does not support tools"}')
            return super().post(url, json, timeout)

    fake = NoTools({"role": "assistant", "content": "General advice."})
    reply = CareerAssistant(AssistantSettings(enabled=True, provider="ollama"), client=fake).reply(
        history=[], message="hi", personal_context=None, executor=SimpleNamespace(run=None)
    )
    assert reply.text == "General advice."


def test_local_rewrite_keeps_privacy_and_checks() -> None:
    fake = FakeOllama({"role": "assistant", "content": json.dumps(_improved_json())})
    rewriter = CvRewriter(CvRewriteSettings(provider="ollama"), client=fake)
    original = parse_cv_text(STRONG_CV)
    result = rewriter.rewrite(original, language="en")
    sent = json.dumps(fake.calls[0]["messages"])
    assert "amira.bensalah@example.com" not in sent and fake.calls[0]["format"]["type"] == "object"
    assert result.improved.contact.email == original.contact.email and result.model.startswith("ollama:")
    assert "Carrefour" in " ".join(s for section in result.sections for s in section.warnings)


def test_ollama_grade_uses_the_rubric_schema() -> None:
    grade = {"assessment": "Solid.", **{c: 6 for c in CRITERION_IDS}, "overall": 61}
    fake = FakeOllama({"role": "assistant", "content": json.dumps(grade)})
    label = ollama_grade(LabelCv("1", "IT", "CV text"), model="m", client=fake)
    assert label["overall"] == 61 and "assessment" not in label
    assert fake.calls[0]["format"]["required"][0] == "assessment" and fake.calls[0]["options"]["temperature"] == 0

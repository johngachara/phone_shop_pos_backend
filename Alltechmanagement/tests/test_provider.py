"""The Cerebras-first, NVIDIA-fallback model chain.

Mocks the OpenAI client entirely -- these are chain-selection and fallback
semantics, not a live model call (see the Build Log for the live comparison
that picked these models and orderings).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from Alltechmanagement.ai import provider


@pytest.fixture(autouse=True)
def reset_client_cache(monkeypatch):
    # The client is a lazy singleton per provider; a stale mock from an
    # earlier test must not leak into the next one.
    monkeypatch.setattr(provider, "_clients", {})
    monkeypatch.setenv("CEREBRAS_API_KEY", "test-cerebras-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    monkeypatch.delenv("CEREBRAS_CHAT_MODELS", raising=False)
    monkeypatch.delenv("CEREBRAS_REPORT_MODELS", raising=False)
    monkeypatch.delenv("NVIDIA_MODELS", raising=False)
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)


def fake_response(text="ok"):
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_chat_mode_tries_cerebras_qwen_first():
    with patch.object(provider, "OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = fake_response()
        mock_openai.return_value = mock_client

        provider.chat([{"role": "user", "content": "hi"}], mode="chat")

        mock_openai.assert_called_once_with(
            base_url=provider.CEREBRAS_BASE_URL, api_key="test-cerebras-key",
            timeout=provider.ATTEMPT_TIMEOUT_SECONDS,
        )
        called_model = mock_client.chat.completions.create.call_args.kwargs["model"]
        assert called_model == "qwen-3.8-27b"


def test_report_mode_tries_cerebras_gpt_oss_120b_first():
    with patch.object(provider, "OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = fake_response()
        mock_openai.return_value = mock_client

        provider.chat([{"role": "user", "content": "hi"}], mode="report")

        called_model = mock_client.chat.completions.create.call_args.kwargs["model"]
        assert called_model == "gpt-oss-120b"


def test_report_is_the_default_mode():
    """GPTAgent.py calls chat() without a mode kwarg -- this must not silently
    start using the interactive chat's model chain."""
    with patch.object(provider, "OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = fake_response()
        mock_openai.return_value = mock_client

        provider.chat([{"role": "user", "content": "hi"}])

        called_model = mock_client.chat.completions.create.call_args.kwargs["model"]
        assert called_model == "gpt-oss-120b"


def test_falls_back_to_nvidia_when_cerebras_fails():
    with patch.object(provider, "OpenAI") as mock_openai:
        cerebras_client = MagicMock()
        cerebras_client.chat.completions.create.side_effect = RuntimeError("rate limited")
        nvidia_client = MagicMock()
        nvidia_client.chat.completions.create.return_value = fake_response("from nvidia")
        mock_openai.side_effect = [cerebras_client, nvidia_client]

        message = provider.chat([{"role": "user", "content": "hi"}], mode="chat")

        assert message.content == "from nvidia"
        nvidia_call = nvidia_client.chat.completions.create.call_args.kwargs
        assert nvidia_call["model"] == provider.DEFAULT_NVIDIA_MODELS[0]


def test_missing_cerebras_key_falls_through_to_nvidia(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    with patch.object(provider, "OpenAI") as mock_openai:
        nvidia_client = MagicMock()
        nvidia_client.chat.completions.create.return_value = fake_response("from nvidia")
        mock_openai.return_value = nvidia_client

        message = provider.chat([{"role": "user", "content": "hi"}], mode="chat")

        assert message.content == "from nvidia"
        mock_openai.assert_called_once_with(
            base_url=provider.NIM_BASE_URL, api_key="test-nvidia-key",
            timeout=provider.ATTEMPT_TIMEOUT_SECONDS,
        )


def test_every_model_failing_raises_ai_unavailable(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    with pytest.raises(provider.AIUnavailable):
        provider.chat([{"role": "user", "content": "hi"}], mode="chat")


def test_env_override_replaces_the_default_chat_model(monkeypatch):
    monkeypatch.setenv("CEREBRAS_CHAT_MODELS", "llama-3.3-70b")
    with patch.object(provider, "OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = fake_response()
        mock_openai.return_value = mock_client

        provider.chat([{"role": "user", "content": "hi"}], mode="chat")

        called_model = mock_client.chat.completions.create.call_args.kwargs["model"]
        assert called_model == "llama-3.3-70b"

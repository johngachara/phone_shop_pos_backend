"""NVIDIA NIM chat client.

Replaces GitHub Models, which was previously called from the browser with a
token shipped in the bundle (VITE_GITHUB_TOKEN), readable by every POS user.
All model calls happen server-side now.

Model choice was made by testing tool-calling against this account rather than
from documentation: of the candidates, nemotron-3-super-120b-a12b and
nemotron-3.5-lightning-30b-a3b both return well-formed tool calls, while
mistral-large-2-instruct and nemotron-nano-3-30b-a3b return 404 -- they are not
deployed for this key. The default chain therefore contains only models proven
to work here.
"""
import logging
import os

from openai import OpenAI

logger = logging.getLogger('django')

NIM_BASE_URL = os.getenv('NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1')

# Primary first. The larger model reasons better over sales data; the lightning
# model is the fallback when the primary is slow or unavailable, and is fast
# enough that a till is not left waiting.
DEFAULT_MODELS = (
    'nvidia/nemotron-3-super-120b-a12b',
    'nvidia/nemotron-3.5-lightning-30b-a3b',
)

# A till operator will not wait indefinitely, and a stalled model should fail
# over rather than hold the request open.
ATTEMPT_TIMEOUT_SECONDS = float(os.getenv('AI_ATTEMPT_TIMEOUT', '45'))


class AIUnavailable(RuntimeError):
    """No configured model could answer."""


def configured_models():
    raw = os.getenv('NVIDIA_MODELS') or os.getenv('NVIDIA_MODEL')
    if raw:
        return tuple(m.strip() for m in raw.split(',') if m.strip())
    return DEFAULT_MODELS


_client = None


def get_client():
    """Built on first use, not at import.

    Importing this module must not require credentials: views.py imports the
    AI surface unconditionally, and a missing key previously took the whole
    process down at startup rather than failing only the AI endpoints.
    """
    global _client
    if _client is None:
        api_key = os.getenv('NVIDIA_API_KEY')
        if not api_key:
            raise AIUnavailable('NVIDIA_API_KEY is not configured')
        _client = OpenAI(base_url=NIM_BASE_URL, api_key=api_key,
                         timeout=ATTEMPT_TIMEOUT_SECONDS)
    return _client


def chat(messages, tools=None, temperature=0.2, max_tokens=1200):
    """Call the first model that answers.

    Returns the assistant message. Raises AIUnavailable when every model in the
    chain fails, so the caller can say so plainly rather than returning an
    empty answer that looks like a real one.
    """
    client = get_client()
    last_error = None

    for model in configured_models():
        try:
            kwargs = {
                'model': model,
                'messages': messages,
                'temperature': temperature,
                'max_tokens': max_tokens,
            }
            if tools:
                kwargs['tools'] = tools
                kwargs['tool_choice'] = 'auto'

            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message
        except Exception as exc:
            last_error = exc
            logger.warning("AI model %s failed, trying next: %s", model, exc)

    raise AIUnavailable(f'All configured models failed: {last_error}')

"""NVIDIA NIM chat client.

Replaces GitHub Models, which was previously called from the browser with a
token shipped in the bundle (VITE_GITHUB_TOKEN), readable by every POS user.
All model calls happen server-side now.

Model choice was made by testing tool-calling against this account rather than
from documentation, re-verified 2026-09-14 after most of the original
candidates (mistral-large-2-instruct, nemotron-nano-3-30b-a3b, every
meta/llama-3.1-3.3 chat model) started returning 404/410 -- either never
deployed for this key or retired from the catalog outright.

nemotron-3.5-lightning-30b-a3b, briefly tried as the primary model for speed,
made things worse rather than better: it is a *reasoning* model that spends
real generation time on a hidden chain-of-thought before it answers or emits a
tool call, and `ai_chat` can chain up to MAX_TOOL_ROUNDS (6) of those before
the user sees a reply. At 45s per attempt with a two-model fallback chain,
that is comfortably enough to blow past gunicorn's 60s worker timeout on any
turn needing more than one round -- confirmed live: `WORKER TIMEOUT` killed
the request outright, not just answered slowly.

openai/gpt-oss-20b replaces it: not a reasoning model, ~3.5s per call in
testing against this account, and produces clean structured tool_calls with
no chain-of-thought leaking into the answer shown to the user.
nemotron-3-super-120b-a12b stays as the fallback -- still the better reasoner
over sales data, for when gpt-oss-20b is slow or unavailable.
"""
import logging
import os

from openai import OpenAI

logger = logging.getLogger('django')

NIM_BASE_URL = os.getenv('NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1')

DEFAULT_MODELS = (
    'openai/gpt-oss-20b',
    'nvidia/nemotron-3-super-120b-a12b',
)

# A till operator will not wait indefinitely, and a stalled model should fail
# over rather than hold the request open. Lowered from 45s: gpt-oss-20b
# answers in single-digit seconds in practice, and a large per-attempt budget
# is exactly what let one slow model attempt, let alone a fallback to a
# second, run past gunicorn's own 60s worker timeout and get killed rather
# than fail over cleanly.
ATTEMPT_TIMEOUT_SECONDS = float(os.getenv('AI_ATTEMPT_TIMEOUT', '20'))


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

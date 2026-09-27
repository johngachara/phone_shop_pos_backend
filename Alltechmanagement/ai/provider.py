"""LLM chat client, with two independent model chains.

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

2026-09-27: Cerebras added ahead of NVIDIA in both chains for latency --
till operators felt the interactive assistant's NVIDIA round-trip. Tested
live against this account with the same tool schema and a representative
report prompt: both Cerebras models answer in ~0.7-0.8s versus ~5s for NVIDIA's
openai/gpt-oss-20b, and gpt-oss-120b's report output was equivalent in
substance and structure to the existing NVIDIA report. Two chains, not one,
because Cerebras's free tier caps gpt-oss-120b at 5 requests/minute and
2,400/day -- fine for two scheduled reports a day, wrong for an interactive
chat that can burn several requests in one reply and is used by more than one
till at once. qwen-3.8-27b's limit (450/min, 648,000/day) fits that instead.
NVIDIA stays as the fallback tail on both chains so a Cerebras outage or rate
limit degrades to the previous behaviour rather than failing outright.
"""
import logging
import os

from openai import OpenAI

logger = logging.getLogger('django')

NIM_BASE_URL = os.getenv('NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1')
CEREBRAS_BASE_URL = os.getenv('CEREBRAS_BASE_URL', 'https://api.cerebras.ai/v1')

DEFAULT_NVIDIA_MODELS = (
    'openai/gpt-oss-20b',
    'nvidia/nemotron-3-super-120b-a12b',
)

# Interactive assistant: throughput matters more than squeezing out the last
# bit of quality, since a chat turn can make several calls and several tills
# can be talking to it at once.
DEFAULT_CEREBRAS_CHAT_MODELS = ('qwen-3.8-27b',)

# Scheduled daily/weekly reports: two calls a day, so quality is the only
# thing that matters and gpt-oss-120b's tight free-tier budget is a non-issue.
DEFAULT_CEREBRAS_REPORT_MODELS = ('gpt-oss-120b',)

# A till operator will not wait indefinitely, and a stalled model should fail
# over rather than hold the request open. Lowered from 45s: gpt-oss-20b
# answers in single-digit seconds in practice, and a large per-attempt budget
# is exactly what let one slow model attempt, let alone a fallback to a
# second, run past gunicorn's own 60s worker timeout and get killed rather
# than fail over cleanly.
ATTEMPT_TIMEOUT_SECONDS = float(os.getenv('AI_ATTEMPT_TIMEOUT', '20'))


class AIUnavailable(RuntimeError):
    """No configured model could answer."""


def _configured_models(env_vars, default):
    for env_var in env_vars:
        raw = os.getenv(env_var)
        if raw:
            return tuple(m.strip() for m in raw.split(',') if m.strip())
    return default


def configured_models():
    """NVIDIA models only. Kept for backward compatibility with anything
    still importing it directly."""
    return _configured_models(('NVIDIA_MODELS', 'NVIDIA_MODEL'), DEFAULT_NVIDIA_MODELS)


def _chain_for(mode):
    """List of (provider, model) pairs to try in order for this mode."""
    nvidia_models = configured_models()
    if mode == 'chat':
        cerebras_models = _configured_models(('CEREBRAS_CHAT_MODELS', 'CEREBRAS_CHAT_MODEL'),
                                             DEFAULT_CEREBRAS_CHAT_MODELS)
    else:
        cerebras_models = _configured_models(('CEREBRAS_REPORT_MODELS', 'CEREBRAS_REPORT_MODEL'),
                                             DEFAULT_CEREBRAS_REPORT_MODELS)

    return (
        [('cerebras', m) for m in cerebras_models]
        + [('nvidia', m) for m in nvidia_models]
    )


_clients = {}


def get_client():
    """NVIDIA client. Kept for backward compatibility."""
    return _get_client('nvidia')


def _get_client(provider):
    """Built on first use, not at import.

    Importing this module must not require credentials: views.py imports the
    AI surface unconditionally, and a missing key previously took the whole
    process down at startup rather than failing only the AI endpoints.
    """
    if provider not in _clients:
        if provider == 'cerebras':
            api_key = os.getenv('CEREBRAS_API_KEY')
            if not api_key:
                raise AIUnavailable('CEREBRAS_API_KEY is not configured')
            _clients[provider] = OpenAI(base_url=CEREBRAS_BASE_URL, api_key=api_key,
                                        timeout=ATTEMPT_TIMEOUT_SECONDS)
        else:
            api_key = os.getenv('NVIDIA_API_KEY')
            if not api_key:
                raise AIUnavailable('NVIDIA_API_KEY is not configured')
            _clients[provider] = OpenAI(base_url=NIM_BASE_URL, api_key=api_key,
                                        timeout=ATTEMPT_TIMEOUT_SECONDS)
    return _clients[provider]


def chat(messages, tools=None, temperature=0.2, max_tokens=1200, mode='report'):
    """Call the first model that answers, trying Cerebras before NVIDIA.

    `mode` selects which Cerebras model chain to use ('chat' for the
    interactive assistant, 'report' for the scheduled insight jobs); NVIDIA is
    the common fallback tail for both. Returns the assistant message. Raises
    AIUnavailable when every model in the chain fails, so the caller can say
    so plainly rather than returning an empty answer that looks like a real
    one.
    """
    last_error = None

    for provider, model in _chain_for(mode):
        try:
            client = _get_client(provider)
        except AIUnavailable as exc:
            last_error = exc
            continue

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
            logger.warning("AI model %s/%s failed, trying next: %s", provider, model, exc)

    raise AIUnavailable(f'All configured models failed: {last_error}')

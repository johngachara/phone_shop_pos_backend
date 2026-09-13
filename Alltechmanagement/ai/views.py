"""Alltech AI endpoints.

Two endpoints, and the split between them is the point: the model can propose a
change, but only a person can cause one.

  POST /api/ai/chat/     runs the model and its read tools
  POST /api/ai/confirm/  executes a change the user accepted

A write tool never executes during the chat turn. It produces a pending action
with a plain-language description, which the POS shows in a confirmation
dialog. Nothing reaches the database until the user comes back through confirm.
"""
import json
import logging
import uuid

from django.core.cache import cache
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response

from Alltechmanagement.ai import tools as ai_tools
from Alltechmanagement.ai.provider import AIUnavailable, chat
from Alltechmanagement.permissions import IsEmployeeOrManager
from Alltechmanagement.throttles import InventoryCheckThrottle, InventoryModificationThrottle

logger = logging.getLogger('django')

# Long enough to read a dialog and decide, short enough that a proposal cannot
# be accepted much later, against data that has since changed.
PENDING_TTL_SECONDS = 300

MAX_TOOL_ROUNDS = 6
MAX_MESSAGES = 30

SYSTEM_PROMPT = """You are Alltech AI, the assistant inside the Alltech phone shop POS.

You can read stock, accessories and sales figures, and you can propose changes
to stock items. You cannot sell, complete or refund anything, and you cannot
change accessories or user accounts.

Rules:
- Use the tools rather than guessing. If you do not have a figure, fetch it.
- When a change is needed, call the matching tool. It will not take effect
  immediately; the user is shown exactly what you proposed and must confirm.
- Never claim a change has been made. Say what you have proposed.
- If sales_summary returns profit_covers_sales lower than sales_count, say that
  profit only covers part of the sales rather than presenting it as complete.
- Prices are Kenyan shillings. Be concise; this is used on a shop counter.
"""


def _pending_key(user_id, action_id):
    return f'ai_pending_{user_id}_{action_id}'


@api_view(['POST'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryCheckThrottle])
def ai_chat(request):
    history = request.data.get('messages') or []
    if not isinstance(history, list) or not history:
        return Response({'error': 'messages must be a non-empty list.'}, status=400)
    if len(history) > MAX_MESSAGES:
        # Bounds the context a caller can push through a paid model.
        history = history[-MAX_MESSAGES:]

    messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    for item in history:
        role = item.get('role')
        if role in ('user', 'assistant') and item.get('content'):
            messages.append({'role': role, 'content': str(item['content'])[:4000]})

    pending = []
    used_tools = []

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            message = chat(messages, tools=ai_tools.TOOL_SCHEMAS)
            tool_calls = getattr(message, 'tool_calls', None)

            if not tool_calls:
                return Response({
                    'reply': message.content or '',
                    'pending_actions': pending,
                    'tools_used': used_tools,
                })

            messages.append({
                'role': 'assistant',
                'content': message.content or '',
                'tool_calls': [
                    {'id': c.id, 'type': 'function',
                     'function': {'name': c.function.name, 'arguments': c.function.arguments}}
                    for c in tool_calls
                ],
            })

            for call in tool_calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or '{}')
                except json.JSONDecodeError:
                    args = {}

                if name in ai_tools.READ_TOOLS:
                    used_tools.append(name)
                    try:
                        result = ai_tools.READ_TOOLS[name](request.user, **args)
                    except Exception as exc:
                        logger.error("AI read tool %s failed: %s", name, exc)
                        result = {'error': 'That lookup failed.'}

                elif name in ai_tools.WRITE_EXECUTORS:
                    # Proposed only. Nothing is written on this path.
                    action_id = uuid.uuid4().hex
                    description = ai_tools.describe_action(name, args)
                    cache.set(
                        _pending_key(request.user.id, action_id),
                        {'tool': name, 'args': args, 'description': description},
                        PENDING_TTL_SECONDS,
                    )
                    pending.append({
                        'action_id': action_id,
                        'tool': name,
                        'arguments': args,
                        'description': description,
                    })
                    result = {
                        'status': 'awaiting_confirmation',
                        'message': 'Proposed. The user must confirm before this happens.',
                    }

                else:
                    result = {'error': f'Unknown tool {name}.'}

                messages.append({
                    'role': 'tool',
                    'tool_call_id': call.id,
                    'content': json.dumps(result, default=str)[:8000],
                })

        # Ran out of rounds. Better to say so than to return the last
        # half-finished thought as if it were an answer.
        return Response({
            'reply': "I couldn't finish that in a reasonable number of steps. "
                     "Try asking for one thing at a time.",
            'pending_actions': pending,
            'tools_used': used_tools,
        })

    except AIUnavailable as exc:
        logger.error("AI unavailable: %s", exc)
        return Response(
            {'error': 'The assistant is unavailable right now.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


@api_view(['POST'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryModificationThrottle])
def ai_confirm(request):
    action_id = request.data.get('action_id')
    if not action_id:
        return Response({'error': 'action_id is required.'}, status=400)

    # Keyed by user: one person cannot confirm an action proposed to another.
    key = _pending_key(request.user.id, action_id)
    pending = cache.get(key)
    if not pending:
        return Response(
            {'error': 'That action has expired or was already handled.'}, status=404
        )

    # Deleted before execution, so a double-submitted dialog cannot run the
    # same change twice.
    cache.delete(key)

    executor = ai_tools.WRITE_EXECUTORS.get(pending['tool'])
    if executor is None:
        return Response({'error': 'Unknown action.'}, status=400)

    try:
        response = executor(request.user, pending['args'])
    except Exception as exc:
        logger.error("AI action %s failed: %s", pending['tool'], exc)
        return Response({'error': 'That action could not be completed.'}, status=500)

    logger.info("User %s confirmed AI action %s: %s",
                request.user.id, pending['tool'], pending['description'])

    body = getattr(response, 'data', None)
    return Response(
        {
            'executed': response.status_code < 400,
            'status_code': response.status_code,
            'description': pending['description'],
            'result': body,
        },
        status=status.HTTP_200_OK if response.status_code < 400 else response.status_code,
    )

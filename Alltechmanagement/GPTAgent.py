"""Scheduled sales insights.

Rewritten onto NVIDIA NIM and the ORM. Previously this called GitHub Models and
read Supabase over its REST client using hardcoded table names
("Alltechmanagement_receipts2_fix"). That coupling was invisible to the type
checker and to every test: renaming a table did not break an import, it just
returned empty results at runtime and produced a confident, wrong report.

The same read tools the interactive assistant uses back these reports, so there
is one description of what the data means rather than two that can drift.
"""
import logging

from Alltechmanagement.ai.provider import AIUnavailable, chat
from Alltechmanagement.ai import tools as ai_tools

logger = logging.getLogger('django')

SYSTEM_PROMPT = """You are Alltech AI writing a sales report for the shop owner.

Use only the figures given to you. Do not invent numbers, and do not estimate
anything you were not given. Where profit covers fewer sales than the total,
say so plainly rather than presenting it as the whole picture.

Write in short sections with bullet points. Amounts are Kenyan shillings.
"""


def run_conversation(user_prompt, days=1):
    """Produce a written report over the last `days` of sales.

    Data is gathered first and handed to the model as facts, rather than letting
    the model fetch it: this runs unattended on a schedule, so there is nobody
    to notice a tool loop going wrong.
    """
    try:
        summary = ai_tools.sales_summary(user=None, days=days)
        stock = ai_tools.low_stock(user=None, threshold=3)
    except Exception as exc:
        logger.error("Could not gather report data: %s", exc)
        raise

    facts = (
        f"Sales over the last {summary['days']} day(s):\n"
        f"- sales: {summary['sales_count']}\n"
        f"- revenue: {summary['revenue']}\n"
        f"- profit: {summary['profit']} "
        f"(covering {summary['profit_covers_sales']} of {summary['sales_count']} sales)\n"
        f"- best sellers: {summary['top_products']}\n\n"
        f"Items at or below 3 in stock: {stock['items']}\n"
    )

    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': f"{user_prompt}\n\nFigures:\n{facts}"},
    ]

    try:
        message = chat(messages, temperature=0.3, max_tokens=1500)
    except AIUnavailable as exc:
        logger.error("Report generation unavailable: %s", exc)
        raise

    return message.content or ''

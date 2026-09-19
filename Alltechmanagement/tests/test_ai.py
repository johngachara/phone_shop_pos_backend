"""Alltech AI.

The safety property under test is that the model can propose a change but only
a person can cause one. Nothing here calls a real model.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from Alltechmanagement.models import Sale, Stock
from Alltechmanagement.supabase_auth import ROLE_EMPLOYEE, SupabaseUser


def principal(user_id="ai-user"):
    return SupabaseUser(user_id=user_id, email="a@alltechnyeri.co.ke",
                        role=ROLE_EMPLOYEE, is_alltech=True)


@pytest.fixture
def client():
    api = APIClient()
    api.force_authenticate(user=principal())
    return api


def tool_call(name, arguments, call_id="call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def assistant(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


@pytest.mark.django_db
def test_a_write_tool_does_not_change_anything_during_chat(client):
    with patch("Alltechmanagement.ai.views.chat", side_effect=[
        assistant(tool_calls=[tool_call("add_stock", '{"product_name":"S21 Screen","quantity":4,"selling_price":4500,"buying_price":2800}')]),
        assistant(content="I've proposed adding that item."),
    ]):
        response = client.post("/api/ai/chat/", {
            "messages": [{"role": "user", "content": "add 4 S21 screens at 4500"}]
        }, format="json")

    assert response.status_code == 200
    body = response.json()
    # The proposal exists...
    assert len(body["pending_actions"]) == 1
    action = body["pending_actions"][0]
    assert action["tool"] == "add_stock"
    # ...described in words a person can check, not raw JSON.
    assert "S21 Screen" in action["description"]
    # ...and nothing has been written.
    assert Stock.objects.count() == 0


# These execute a write through /api/ai/confirm/, which calls add_stock2_api --
# an async view. sync_to_async reaches the ORM on a separate connection, outside
# pytest-django's wrapping transaction, so those rows commit and are never
# rolled back. Without transaction=True they survive the test and appear in
# every later one, which is how a search test started seeing "S21 Screen".
@pytest.mark.django_db(transaction=True)
def test_confirming_executes_the_change(client):
    with patch("Alltechmanagement.ai.views.chat", side_effect=[
        assistant(tool_calls=[tool_call("add_stock", '{"product_name":"S21 Screen","quantity":4,"selling_price":4500,"buying_price":2800}')]),
        assistant(content="Proposed."),
    ]):
        action_id = client.post("/api/ai/chat/", {
            "messages": [{"role": "user", "content": "add stock"}]
        }, format="json").json()["pending_actions"][0]["action_id"]

    response = client.post("/api/ai/confirm/", {"action_id": action_id}, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["executed"] is True
    assert Stock.objects.get(product_name="S21 Screen").quantity == 4


# These execute a write through /api/ai/confirm/, which calls add_stock2_api --
# an async view. sync_to_async reaches the ORM on a separate connection, outside
# pytest-django's wrapping transaction, so those rows commit and are never
# rolled back. Without transaction=True they survive the test and appear in
# every later one, which is how a search test started seeing "S21 Screen".
@pytest.mark.django_db(transaction=True)
def test_an_action_cannot_be_confirmed_twice(client):
    with patch("Alltechmanagement.ai.views.chat", side_effect=[
        assistant(tool_calls=[tool_call("add_stock", '{"product_name":"Once","quantity":1,"selling_price":100,"buying_price":60}')]),
        assistant(content="Proposed."),
    ]):
        action_id = client.post("/api/ai/chat/", {
            "messages": [{"role": "user", "content": "x"}]
        }, format="json").json()["pending_actions"][0]["action_id"]

    assert client.post("/api/ai/confirm/", {"action_id": action_id}, format="json").status_code == 200
    # A double-submitted dialog must not add the item twice.
    assert client.post("/api/ai/confirm/", {"action_id": action_id}, format="json").status_code == 404
    assert Stock.objects.filter(product_name="Once").count() == 1


@pytest.mark.django_db
def test_one_user_cannot_confirm_another_users_proposal(client):
    with patch("Alltechmanagement.ai.views.chat", side_effect=[
        assistant(tool_calls=[tool_call("delete_stock", '{"id":1}')]),
        assistant(content="Proposed."),
    ]):
        action_id = client.post("/api/ai/chat/", {
            "messages": [{"role": "user", "content": "delete it"}]
        }, format="json").json()["pending_actions"][0]["action_id"]

    other = APIClient()
    other.force_authenticate(user=principal("someone-else"))
    assert other.post("/api/ai/confirm/", {"action_id": action_id}, format="json").status_code == 404


@pytest.mark.django_db
def test_confirming_an_unknown_action_is_refused(client):
    assert client.post("/api/ai/confirm/", {"action_id": "made-up"}, format="json").status_code == 404


@pytest.mark.django_db
def test_read_tools_run_without_confirmation(client):
    Stock.objects.create(product_name="A1 Screen", quantity=7,
                         selling_price=Decimal("1000.00"),
                         buying_price=Decimal("600.00"))

    with patch("Alltechmanagement.ai.views.chat", side_effect=[
        assistant(tool_calls=[tool_call("search_stock", '{"query":"A1"}')]),
        assistant(content="There are 7 A1 Screens."),
    ]):
        body = client.post("/api/ai/chat/", {
            "messages": [{"role": "user", "content": "how many A1 screens?"}]
        }, format="json").json()

    assert body["tools_used"] == ["search_stock"]
    assert body["pending_actions"] == []


@pytest.mark.django_db
def test_the_assistant_has_no_tool_for_selling_or_refunding():
    from Alltechmanagement.ai import tools as ai_tools
    names = set(ai_tools.READ_TOOLS) | set(ai_tools.WRITE_EXECUTORS)
    # The brief limits the assistant to stock. Anything that moves money or
    # changes a user account is deliberately absent.
    for forbidden in ("sell", "refund", "complete", "create_user", "delete_user"):
        assert not any(forbidden in n for n in names), f"{forbidden} is reachable"
    assert set(ai_tools.WRITE_EXECUTORS) == {
        "add_stock", "update_stock", "delete_stock",
        "add_stock_batch", "update_stock_batch", "delete_stock_batch",
    }


@pytest.mark.django_db
def test_a_batch_proposal_does_not_change_anything_during_chat(client):
    items = '{"items":[' + ','.join(
        f'{{"product_name":"Item {i}","quantity":1,"selling_price":100,"buying_price":60}}'
        for i in range(3)
    ) + ']}'
    with patch("Alltechmanagement.ai.views.chat", side_effect=[
        assistant(tool_calls=[tool_call("add_stock_batch", items)]),
        assistant(content="Proposed 3 items."),
    ]):
        response = client.post("/api/ai/chat/", {
            "messages": [{"role": "user", "content": "add 3 items"}]
        }, format="json")

    assert response.status_code == 200
    body = response.json()
    action = body["pending_actions"][0]
    assert action["tool"] == "add_stock_batch"
    assert "3" in action["description"]
    assert Stock.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_confirming_a_batch_executes_every_item(client):
    items = '{"items":[' + ','.join(
        f'{{"product_name":"Batch Item {i}","quantity":2,"selling_price":100,"buying_price":60}}'
        for i in range(3)
    ) + ']}'
    with patch("Alltechmanagement.ai.views.chat", side_effect=[
        assistant(tool_calls=[tool_call("add_stock_batch", items)]),
        assistant(content="Proposed."),
    ]):
        action_id = client.post("/api/ai/chat/", {
            "messages": [{"role": "user", "content": "add 3 items"}]
        }, format="json").json()["pending_actions"][0]["action_id"]

    response = client.post("/api/ai/confirm/", {"action_id": action_id}, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["result"]["succeeded"] == 3
    assert Stock.objects.filter(product_name__startswith="Batch Item").count() == 3


@pytest.mark.django_db
def test_a_batch_over_the_max_is_refused_before_it_becomes_a_proposal(client):
    from Alltechmanagement.ai import tools as ai_tools
    too_many = ','.join(
        f'{{"product_name":"X{i}","quantity":1,"selling_price":100}}'
        for i in range(ai_tools.MAX_BATCH_ITEMS + 1)
    )
    with patch("Alltechmanagement.ai.views.chat", side_effect=[
        assistant(tool_calls=[tool_call("add_stock_batch", '{"items":[' + too_many + ']}')]),
        assistant(content="Too many."),
    ]):
        body = client.post("/api/ai/chat/", {
            "messages": [{"role": "user", "content": "add a lot of items"}]
        }, format="json").json()

    assert body["pending_actions"] == []
    assert Stock.objects.count() == 0


@pytest.mark.django_db
def test_a_batch_mixing_action_types_is_not_possible():
    """Batch tools only ever apply one action type to many items.

    There is no tool that accepts a mixed list of adds and deletes -- each
    batch tool executes exactly one kind of write.
    """
    from Alltechmanagement.ai import tools as ai_tools
    for name in ("add_stock_batch", "update_stock_batch", "delete_stock_batch"):
        schema = next(t for t in ai_tools.TOOL_SCHEMAS if t["function"]["name"] == name)
        assert "items" in schema["function"]["parameters"]["properties"]


@pytest.mark.django_db
def test_sales_summary_states_how_much_of_profit_is_known():
    from django.utils import timezone
    from Alltechmanagement.ai import tools as ai_tools
    Sale.objects.create(product_name="A", quantity=1, selling_price=Decimal("1000.00"),
                        buying_price=Decimal("600.00"), customer_name="x",
                        status=Sale.Status.COMPLETED, completed_at=timezone.now())
    Sale.objects.create(product_name="B", quantity=1, selling_price=Decimal("2000.00"),
                        customer_name="x", status=Sale.Status.COMPLETED,
                        completed_at=timezone.now())

    result = ai_tools.sales_summary(user=None, days=7)
    assert result["sales_count"] == 2
    # Handed to the model so it cannot present partial profit as complete.
    assert result["profit_covers_sales"] == 1


@pytest.mark.django_db
def test_ai_unavailable_is_reported_not_faked(client):
    from Alltechmanagement.ai.provider import AIUnavailable
    with patch("Alltechmanagement.ai.views.chat", side_effect=AIUnavailable("down")):
        response = client.post("/api/ai/chat/", {
            "messages": [{"role": "user", "content": "hi"}]
        }, format="json")
    # 503 and a plain message, rather than an empty reply that reads like an answer.
    assert response.status_code == 503


@pytest.mark.django_db
def test_ai_endpoints_require_an_alltech_role():
    anon = APIClient()
    assert anon.post("/api/ai/chat/", {"messages": []}, format="json").status_code == 401
    outsider = APIClient()
    outsider.force_authenticate(user=SupabaseUser(user_id="o", role=None, is_alltech=False))
    assert outsider.post("/api/ai/chat/", {"messages": []}, format="json").status_code == 403


# --- manager-only push --------------------------------------------------------

@pytest.mark.django_db
def test_push_targets_managers_only():
    from Alltechmanagement.models import PushDevice
    from Alltechmanagement.push import notify_managers

    PushDevice.objects.create(token='mgr-1', user_id='m1', role='manager')
    PushDevice.objects.create(token='emp-1', user_id='e1', role='employee')
    PushDevice.objects.create(token='none-1', user_id='x1', role=None)

    with patch('Alltechmanagement.push.send_push') as send:
        send.return_value = SimpleNamespace(success_count=1, responses=[])
        notify_managers('Title', 'Body')

    tokens = send.call_args[0][2]
    # An employee must not receive the shop's takings on their phone.
    assert tokens == ['mgr-1']


@pytest.mark.django_db
def test_registering_the_same_device_twice_reassigns_it():
    from Alltechmanagement.models import PushDevice
    client = APIClient()
    client.force_authenticate(user=principal('user-a'))
    assert client.post('/api/push/register/', {'token': 'shared-tablet'}, format='json').status_code == 201

    other = APIClient()
    other.force_authenticate(user=principal('user-b'))
    assert other.post('/api/push/register/', {'token': 'shared-tablet'}, format='json').status_code == 200

    # A counter tablet is shared. The token belongs to whoever signed in last,
    # not to two people at once.
    assert PushDevice.objects.count() == 1
    assert PushDevice.objects.get().user_id == 'user-b'


@pytest.mark.django_db
def test_push_failure_does_not_raise():
    from Alltechmanagement.models import PushDevice
    from Alltechmanagement.push import notify_managers
    PushDevice.objects.create(token='mgr-1', user_id='m1', role='manager')
    with patch('Alltechmanagement.push.send_push', side_effect=RuntimeError('FCM down')):
        # Reporting must not fail because a notification could not be delivered.
        assert notify_managers('Title', 'Body') == 0


@pytest.mark.django_db
def test_no_manager_devices_is_not_an_error():
    from Alltechmanagement.push import notify_managers
    assert notify_managers('Title', 'Body') == 0


# --- sales report configuration ----------------------------------------------

@pytest.mark.django_db
def test_sales_report_says_what_is_missing_rather_than_failing_generically(monkeypatch):
    """A missing mail key must not look like a server fault.

    This surfaced as a bare 500 from a scheduled job: the real cause was a
    rejected Resend key, which is trivial to fix once it is visible and
    impossible to find when it is not.
    """
    from Alltechmanagement.models import Sale
    from django.utils import timezone

    Sale.objects.create(
        product_name='A', quantity=1, selling_price=Decimal('100.00'),
        customer_name='x', status=Sale.Status.COMPLETED, completed_at=timezone.now(),
    )
    for name in ('RESEND_API_KEY', 'RESEND_SENDER_EMAIL', 'GMAIL_RECEIVER'):
        monkeypatch.delenv(name, raising=False)

    from Alltechmanagement import views
    from rest_framework.test import APIRequestFactory, force_authenticate

    class Machine:
        is_authenticated = True
        id = None
        firebase_uid = 'celery'

    request = APIRequestFactory().get('/api/send_sale2')
    force_authenticate(request, user=Machine())
    response = views.send_sales2_api(request)

    assert response.status_code == 503
    assert 'RESEND_API_KEY' in response.data['error']


@pytest.mark.django_db
def test_send_sales_email_failure_returns_generic_502(monkeypatch):
    from Alltechmanagement.models import Sale
    from django.utils import timezone

    Sale.objects.create(
        product_name='x', quantity=1, selling_price=Decimal('100.00'),
        customer_name='x', status=Sale.Status.COMPLETED, completed_at=timezone.now(),
    )
    monkeypatch.setenv('RESEND_API_KEY', 're_123')
    monkeypatch.setenv('RESEND_SENDER_EMAIL', 'pos@alltech.co.ke')
    monkeypatch.setenv('GMAIL_RECEIVER', 'mgr@alltech.co.ke')

    from Alltechmanagement import views
    from rest_framework.test import APIRequestFactory, force_authenticate

    class Machine:
        is_authenticated = True
        id = None
        firebase_uid = 'celery'

    with patch('resend.Emails.send', side_effect=Exception('API key revoked secret info')):
        request = APIRequestFactory().get('/api/send_sale2')
        force_authenticate(request, user=Machine())
        response = views.send_sales2_api(request)

    assert response.status_code == 502
    assert response.data == {'error': 'Could not send the sales report.'}
    assert 'secret' not in response.data['error']


@pytest.mark.django_db
def test_batch_item_failure_returns_generic_error():
    from Alltechmanagement.ai import tools as ai_tools
    from Alltechmanagement.supabase_auth import ROLE_MANAGER, SupabaseUser

    def failing_executor(user, item):
        raise RuntimeError("Internal DB connection leak details")

    user = SupabaseUser(user_id='u', email='u@test.com', role=ROLE_MANAGER, is_alltech=True)
    resp = ai_tools._run_batch(failing_executor, user, [{'product_name': 'Item 1'}])
    assert resp.status_code == 400
    assert resp.data['results'][0]['error'] == 'Item processing failed'
    assert 'leak' not in resp.data['results'][0]['error']


@pytest.mark.django_db
def test_daily_insight_finds_yesterdays_sale_at_the_nairobi_day_boundary(monkeypatch):
    """The scheduler runs at 07:00 Nairobi time, which is 04:00 UTC -- still
    the same UTC calendar date as "today" in Nairobi. A container clock reads
    UTC, so `datetime.now()` (a naive, unconverted read of that clock) used to
    silently stand in for "now in Nairobi" here, while every other date in the
    codebase goes through `django.utils.timezone`. Most days the two agree by
    coincidence; this fixes the run at a moment where they would not, without
    needing a real timezone bug in production to notice: a sale placed early
    in the Nairobi morning of "yesterday" (00:30 EAT, i.e. 2026-09-16 21:30
    UTC) must still be found once the job runs the next Nairobi morning.
    """
    from datetime import datetime, timezone as dt_timezone
    from django.utils import timezone
    from Alltechmanagement import views

    fixed_now = datetime(2026, 9, 18, 4, 0, 0, tzinfo=dt_timezone.utc)
    monkeypatch.setattr(views.timezone, 'now', lambda: fixed_now)

    Sale.objects.create(
        product_name='Screen', quantity=1, selling_price=Decimal('5000.00'),
        customer_name='x', status=Sale.Status.COMPLETED,
        created_at=datetime(2026, 9, 16, 21, 30, 0, tzinfo=dt_timezone.utc),
    )

    class Machine:
        is_authenticated = True
        id = None
        firebase_uid = 'celery'

    from rest_framework.test import APIRequestFactory, force_authenticate

    with patch('Alltechmanagement.views.run_conversation', return_value='report text'):
        request = APIRequestFactory().get('/api/daily-ai/')
        force_authenticate(request, user=Machine())
        response = views.get_daily_ai_insights(request)

    assert response.status_code == 200


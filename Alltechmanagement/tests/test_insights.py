"""Stored sales reports.

These exist so a push notification can link to the whole report instead of the
240 characters an operating system will show. Before this the text was returned
to whatever triggered the job and discarded.
"""
import pytest
from rest_framework.test import APIClient

from Alltechmanagement.models import Insight
from Alltechmanagement.supabase_auth import ROLE_EMPLOYEE, ROLE_MANAGER, SupabaseUser


def client_as(role):
    api = APIClient()
    api.force_authenticate(user=SupabaseUser(
        user_id='u', email='u@alltechnyeri.co.ke', role=role, is_alltech=True))
    return api


@pytest.fixture
def report(db):
    return Insight.objects.create(
        kind=Insight.Kind.DAILY,
        title='Sales for Friday 12 September',
        body='Full report text.\n\n- One thing\n- Another thing',
    )


@pytest.mark.django_db
def test_manager_can_read_a_report(report):
    body = client_as(ROLE_MANAGER).get(f'/api/insights/{report.id}/').json()
    assert body['title'] == report.title
    # The whole text, not the truncated notification body.
    assert body['body'] == report.body


@pytest.mark.django_db
def test_the_list_omits_bodies(report):
    body = client_as(ROLE_MANAGER).get('/api/insights/').json()
    assert body['insights'][0]['title'] == report.title
    # A report is kilobytes of prose; fifty of them would make a list of titles
    # a slow response.
    assert 'body' not in body['insights'][0]


@pytest.mark.django_db
def test_employees_cannot_read_reports(report):
    # These are revenue and profit figures in prose.
    assert client_as(ROLE_EMPLOYEE).get('/api/insights/').status_code == 403
    assert client_as(ROLE_EMPLOYEE).get(f'/api/insights/{report.id}/').status_code == 403


@pytest.mark.django_db
def test_anonymous_callers_are_rejected(report):
    assert APIClient().get(f'/api/insights/{report.id}/').status_code == 401


@pytest.mark.django_db
def test_a_missing_report_is_a_404_not_a_crash():
    assert client_as(ROLE_MANAGER).get('/api/insights/99999/').status_code == 404


@pytest.mark.django_db
def test_newest_first(db):
    from django.utils import timezone
    from datetime import timedelta
    old = Insight.objects.create(kind='DAILY', title='Older', body='x',
                                 created_at=timezone.now() - timedelta(days=2))
    new = Insight.objects.create(kind='DAILY', title='Newer', body='y')
    titles = [i['title'] for i in client_as(ROLE_MANAGER).get('/api/insights/').json()['insights']]
    assert titles.index(new.title) < titles.index(old.title)

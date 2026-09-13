"""Stored sales insights.

Manager-only, like everything else that reports money.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response

from Alltechmanagement.models import Insight
from Alltechmanagement.permissions import IsManager
from Alltechmanagement.throttles import InventoryCheckThrottle

# Enough to scroll a couple of months without paginating something nobody
# paginates.
LIST_LIMIT = 50


def present(insight, include_body=True):
    data = {
        'id': insight.id,
        'kind': insight.kind,
        'title': insight.title,
        'created_at': insight.created_at,
        'period_start': insight.period_start,
        'period_end': insight.period_end,
    }
    if include_body:
        data['body'] = insight.body
    return data


@api_view(['GET'])
@permission_classes([IsManager])
@throttle_classes([InventoryCheckThrottle])
def list_insights(request):
    # Bodies are omitted from the list: a report is a few kilobytes of prose
    # and fifty of them is a slow response for a screen that shows titles.
    rows = Insight.objects.all()[:LIST_LIMIT]
    return Response({'insights': [present(i, include_body=False) for i in rows]})


@api_view(['GET'])
@permission_classes([IsManager])
@throttle_classes([InventoryCheckThrottle])
def get_insight(request, insight_id):
    try:
        insight = Insight.objects.get(pk=insight_id)
    except Insight.DoesNotExist:
        return Response({'error': 'Report not found.'}, status=status.HTTP_404_NOT_FOUND)
    return Response(present(insight))

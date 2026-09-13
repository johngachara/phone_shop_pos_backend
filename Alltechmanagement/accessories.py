"""Accessories.

Ported from the `sequelizer` service, where accessories lived in Firestore and
were served from a separate Express app at /sequel/api/*. That service is being
retired; this is the same surface on this backend, against Postgres.

Two things the original did that are not reproduced here:

- Auto-increment ids were simulated with a `counters/accessories` document read
  and written on every insert. Two concurrent inserts could read the same
  counter and collide. Postgres issues the id.
- Selling wrote the same row into both a `completed_sales` and a `receipts`
  collection. Accessory sales are ordinary rows in `sales` now, so they appear
  in reporting alongside screen sales with no duplicate write.
"""
import logging
import os

import meilisearch
from django.core.cache import cache
from django.db import transaction as django_transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response

from Alltechmanagement.models import Accessory, Customer, Sale
from Alltechmanagement.permissions import IsEmployeeOrManager
from Alltechmanagement.serializers import AccessorySerializer, SellSerializer
from Alltechmanagement.throttles import (
    InventoryCheckThrottle,
    InventoryModificationThrottle,
    SalesOperationsThrottle,
)

logger = logging.getLogger('django')

ACCESSORY_INDEX = 'Accessories_New'
CACHE_KEY = 'ACCESSORIES'


def _index():
    client = meilisearch.Client(os.getenv('MEILISEARCH_URL'), os.getenv('MEILISEARCH_KEY'))
    return client.index(ACCESSORY_INDEX)


def _reindex(accessory):
    """Best-effort search index update.

    Search being stale is an inconvenience; refusing the write because the
    search server is down would stop the shop trading.
    """
    try:
        _index().update_documents([{
            'id': accessory.id,
            'product_name': accessory.product_name,
            'price': int(accessory.selling_price),
            'quantity': accessory.quantity,
        }])
    except Exception as exc:
        logger.error("Accessory index update failed for %s: %s", accessory.id, exc)


def _deindex(accessory_id):
    try:
        _index().delete_document(accessory_id)
    except Exception as exc:
        logger.error("Accessory index delete failed for %s: %s", accessory_id, exc)


@api_view(['GET'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryCheckThrottle])
def list_accessories(request):
    try:
        page = max(1, int(request.query_params.get('page', 1)))
        limit = min(100, max(1, int(request.query_params.get('limit', 12))))
    except (TypeError, ValueError):
        return Response({'error': 'page and limit must be integers.'}, status=400)

    queryset = Accessory.objects.all()
    total = queryset.count()
    offset = (page - 1) * limit
    items = queryset[offset:offset + limit]

    return Response({
        'totalItems': total,
        'totalPages': (total + limit - 1) // limit if limit else 0,
        'currentPage': page,
        'items': AccessorySerializer(items, many=True).data,
    })


@api_view(['GET'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryCheckThrottle])
def get_accessory(request, accessory_id):
    try:
        accessory = Accessory.objects.get(pk=accessory_id)
    except Accessory.DoesNotExist:
        return Response({'error': 'Item not found'}, status=404)
    return Response(AccessorySerializer(accessory).data)


@api_view(['POST'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryModificationThrottle])
def add_accessory(request):
    serializer = AccessorySerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    if Accessory.objects.filter(
        product_name__iexact=serializer.validated_data['product_name']
    ).exists():
        return Response({'error': 'Item already exists'}, status=400)

    accessory = serializer.save()
    _reindex(accessory)
    cache.delete(CACHE_KEY)
    return Response(AccessorySerializer(accessory).data, status=status.HTTP_201_CREATED)


@api_view(['PUT', 'PATCH'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryModificationThrottle])
def update_accessory(request, accessory_id):
    try:
        accessory = Accessory.objects.get(pk=accessory_id)
    except Accessory.DoesNotExist:
        return Response({'error': 'Item not found'}, status=404)

    serializer = AccessorySerializer(accessory, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    accessory = serializer.save()
    _reindex(accessory)
    cache.delete(CACHE_KEY)
    return Response(AccessorySerializer(accessory).data)


@api_view(['DELETE'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryModificationThrottle])
def delete_accessory(request, accessory_id):
    try:
        accessory = Accessory.objects.get(pk=accessory_id)
    except Accessory.DoesNotExist:
        return Response({'error': 'Item not found'}, status=404)

    accessory_id = accessory.id
    accessory.delete()
    _deindex(accessory_id)
    cache.delete(CACHE_KEY)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([SalesOperationsThrottle])
def sell_accessory(request, accessory_id):
    """Sell an accessory.

    Completes immediately rather than being held: the original had no unpaid
    state for accessories, and adding one would change how the counter works.
    The row is still a normal Sale, so it reaches reporting the same way a
    screen sale does.
    """
    serializer = SellSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    quantity = serializer.validated_data['quantity']
    customer_name = serializer.validated_data['customer_name'].lower()

    try:
        with django_transaction.atomic():
            accessory = Accessory.objects.select_for_update().get(pk=accessory_id)

            if accessory.quantity < quantity:
                return Response(
                    {'error': 'Cannot sell product, insufficient quantity'}, status=400
                )

            Accessory.objects.filter(pk=accessory.pk).update(
                quantity=F('quantity') - quantity
            )

            sale = Sale.objects.create(
                product_name=accessory.product_name,
                quantity=quantity,
                selling_price=serializer.validated_data['price'],
                # Captured now, for the same reason screen sales capture it.
                buying_price=accessory.buying_price,
                customer_name=customer_name,
                accessory=accessory,
                item_type=Sale.ItemType.ACCESSORY,
                status=Sale.Status.COMPLETED,
                completed_at=timezone.now(),
            )

            customer, created = Customer.objects.get_or_create(
                name=customer_name, defaults={'total_spent': sale.total_amount}
            )
            if not created:
                Customer.objects.filter(pk=customer.pk).update(
                    total_spent=F('total_spent') + sale.total_amount
                )

            accessory.refresh_from_db()
    except Accessory.DoesNotExist:
        return Response({'error': 'Item not found'}, status=404)

    _reindex(accessory)
    cache.delete(CACHE_KEY)

    from Alltechmanagement.admin_apis import invalidate_dashboard_caches
    invalidate_dashboard_caches()

    return Response({'message': 'Sold', 'sale_id': sale.id}, status=200)

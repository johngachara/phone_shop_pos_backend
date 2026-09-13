"""Accessories.

Ported from the `sequelizer` service, where accessories lived in Firestore and
were served from a separate Express app at /sequel/api/*. That service is being
retired; this is the same surface on this backend, against Postgres.

Search is a Postgres filter on this endpoint, not a separate search service.
The Meilisearch index the original maintained was write-only by the end: every
mutation updated it and nothing ever queried it, because the rewritten
frontend asks this API instead.

Two things the original did that are not reproduced here:

- Auto-increment ids were simulated with a `counters/accessories` document read
  and written on every insert. Two concurrent inserts could read the same
  counter and collide. Postgres issues the id.
- Selling wrote the same row into both a `completed_sales` and a `receipts`
  collection. Accessory sales are ordinary rows in `sales` now, so they appear
  in reporting alongside screen sales with no duplicate write.
"""
import logging

from django.core.cache import cache
from django.db import transaction as django_transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response

from Alltechmanagement.models import Accessory, Sale
from Alltechmanagement.permissions import IsEmployeeOrManager
from Alltechmanagement.views import record_customer_spend
from Alltechmanagement.search import search_products
from Alltechmanagement.serializers import AccessorySerializer, SellSerializer
from Alltechmanagement.throttles import (
    InventoryCheckThrottle,
    InventoryModificationThrottle,
    SalesOperationsThrottle,
)

logger = logging.getLogger('django')

CACHE_KEY = 'ACCESSORIES'



@api_view(['GET'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryCheckThrottle])
def list_accessories(request):
    try:
        page = max(1, int(request.query_params.get('page', 1)))
        limit = min(100, max(1, int(request.query_params.get('limit', 12))))
    except (TypeError, ValueError):
        return Response({'error': 'page and limit must be integers.'}, status=400)

    # Filtered here, not in the browser: this endpoint is paginated, so a
    # client-side filter only searches the loaded page and silently reports
    # nothing for items further down the list.
    query = (request.query_params.get('q') or '').strip()
    queryset = Accessory.objects.all()
    if query:
        queryset = search_products(queryset, query)

    # The first unfiltered page is what the accessories screen opens on, so it
    # is worth caching; searches and later pages are not, because caching every
    # distinct query fills Redis with entries used once. The four write paths
    # already delete this key -- until now it was never populated, so those
    # deletes did nothing.
    cacheable = not query and page == 1
    if cacheable:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return Response(cached)

    total = queryset.count()
    offset = (page - 1) * limit
    items = queryset[offset:offset + limit]

    payload = {
        'totalItems': total,
        'totalPages': (total + limit - 1) // limit if limit else 0,
        'currentPage': page,
        'items': AccessorySerializer(items, many=True).data,
    }

    if cacheable:
        cache.set(CACHE_KEY, payload, timeout=60 * 120)

    return Response(payload)


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

    accessory.delete()
    cache.delete(CACHE_KEY)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([SalesOperationsThrottle])
def sell_accessory(request, accessory_id):
    """Sell an accessory, either held or paid.

    Same two paths as a screen, and the same default: holding, because an item
    is often handed over before it is paid for. Accessories were
    complete-only, which meant an accessory handed over on credit had nowhere
    to live -- it was either recorded as paid when it was not, or not recorded
    at all.
    """
    serializer = SellSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    complete_now = bool(request.data.get('complete'))
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
                status=(
                    Sale.Status.COMPLETED if complete_now else Sale.Status.PENDING
                ),
                completed_at=timezone.now() if complete_now else None,
            )

            # A held sale owes nothing yet. The customer's total moves when the
            # order is completed, not when the item leaves the shelf.
            if complete_now:
                record_customer_spend(customer_name, sale.total_amount)

            accessory.refresh_from_db()
    except Accessory.DoesNotExist:
        return Response({'error': 'Item not found'}, status=404)
    cache.delete(CACHE_KEY)

    if complete_now:
        from Alltechmanagement.admin_apis import invalidate_dashboard_caches
        invalidate_dashboard_caches()

    return Response(
        {
            'message': 'Sold' if complete_now else 'On hold',
            'sale_id': sale.id,
            'status': sale.status,
        },
        status=200,
    )

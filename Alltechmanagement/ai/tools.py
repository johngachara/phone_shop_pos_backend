"""Tools the assistant can use.

Two rules shape this file.

**Stock only.** The assistant can read sales and customer figures, but the only
things it can change are stock items. Nothing here sells, completes, refunds or
touches a user account.

**Writes go through the API, never the ORM.** Every write tool is executed by
calling the actual view function with the caller's own principal attached, so
the endpoint's permission class and throttle apply exactly as they would to a
request from the POS. Reaching for the ORM here would create a second path into
the data with none of those checks -- the assistant would be able to do things
the person driving it cannot.
"""
import logging

from rest_framework.test import APIRequestFactory, force_authenticate

logger = logging.getLogger('django')

_factory = APIRequestFactory()


def _call_endpoint(view, method, path, user, data=None, **view_kwargs):
    """Invoke a DRF view as the given principal.

    In-process rather than a real HTTP call to ourselves: a self-request can
    deadlock when every gunicorn worker is busy serving the request that made
    it. The view's decorators -- permission_classes, throttle_classes -- run
    either way, which is the property that matters.
    """
    request = getattr(_factory, method.lower())(path, data, format='json')
    force_authenticate(request, user=user)
    response = view(request, **view_kwargs)
    if hasattr(response, 'render'):
        response.render()
    return response


# --- read tools --------------------------------------------------------------

def search_stock(user, query=None, limit=20):
    from Alltechmanagement.models import Stock
    queryset = Stock.objects.all()
    if query:
        queryset = queryset.filter(product_name__icontains=query)
    items = queryset[:min(int(limit or 20), 50)]
    return {'count': len(items), 'items': [
        {
            'id': i.id,
            'product_name': i.product_name,
            'quantity': i.quantity,
            'selling_price': str(i.selling_price),
            'buying_price': str(i.buying_price) if i.buying_price is not None else None,
        }
        for i in items
    ]}


def low_stock(user, threshold=3):
    from Alltechmanagement.models import Stock
    items = Stock.objects.filter(quantity__lte=int(threshold or 3))[:50]
    return {'threshold': int(threshold or 3), 'count': len(items), 'items': [
        {'id': i.id, 'product_name': i.product_name, 'quantity': i.quantity}
        for i in items
    ]}


def sales_summary(user, days=7):
    from datetime import timedelta

    from django.db.models import Count, F, Sum
    from django.utils import timezone

    from Alltechmanagement.admin_apis import completed_sales, profit_sum, sales_with_cost

    days = min(int(days or 7), 365)
    since = timezone.now() - timedelta(days=days)
    rows = completed_sales().filter(created_at__gte=since)

    totals = rows.aggregate(
        sales_count=Count('id'),
        revenue=Sum(F('selling_price') * F('quantity')),
        profit=profit_sum(),
        sales_with_cost=sales_with_cost(),
    )
    top = list(
        rows.values('product_name')
        .annotate(units=Sum('quantity'), revenue=Sum(F('selling_price') * F('quantity')))
        .order_by('-revenue')[:10]
    )
    return {
        'days': days,
        'sales_count': totals['sales_count'] or 0,
        'revenue': str(totals['revenue'] or 0),
        'profit': str(totals['profit'] or 0),
        # Stated so the assistant does not present profit from a subset as if
        # it covered everything.
        'profit_covers_sales': totals['sales_with_cost'] or 0,
        'top_products': [
            {'product_name': r['product_name'], 'units': r['units'],
             'revenue': str(r['revenue'])}
            for r in top
        ],
    }


def search_accessories(user, query=None, limit=20):
    from Alltechmanagement.models import Accessory
    queryset = Accessory.objects.all()
    if query:
        queryset = queryset.filter(product_name__icontains=query)
    items = queryset[:min(int(limit or 20), 50)]
    return {'count': len(items), 'items': [
        {'id': i.id, 'product_name': i.product_name, 'quantity': i.quantity,
         'selling_price': str(i.selling_price)}
        for i in items
    ]}


# --- write tools (proposed, never executed inline) ---------------------------

def _execute_add_stock(user, args):
    from Alltechmanagement.views import add_stock2_api
    payload = {
        'product_name': args['product_name'],
        'quantity': args['quantity'],
        'selling_price': str(args['selling_price']),
    }
    if args.get('buying_price') is not None:
        payload['buying_price'] = str(args['buying_price'])
    return _call_endpoint(add_stock2_api, 'post', '/api/add_stock2', user, payload)


def _execute_update_stock(user, args):
    from Alltechmanagement.views import update_stock2_api
    payload = {k: v for k, v in args.items() if k != 'id' and v is not None}
    for money in ('selling_price', 'buying_price'):
        if money in payload:
            payload[money] = str(payload[money])
    return _call_endpoint(
        update_stock2_api, 'patch', f"/api/update_stock2/{args['id']}", user,
        payload, id=args['id'],
    )


def _execute_delete_stock(user, args):
    from Alltechmanagement.views import delete_stock2_api
    return _call_endpoint(
        delete_stock2_api, 'delete', f"/api/delete_stock2_api/{args['id']}", user,
        None, id=args['id'],
    )


# A batch call is one action type applied to several items, not several
# different actions bundled together -- e.g. adding 5 new stock items in one
# call, not one add and one delete in the same call. Capped well below what a
# human would want to review in a single confirmation dialog.
MAX_BATCH_ITEMS = 20

BATCH_TOOLS = {'add_stock_batch', 'update_stock_batch', 'delete_stock_batch'}


def validate_batch(name, args):
    """Return an error string, or None when the batch args are usable."""
    if name not in BATCH_TOOLS:
        return None
    items = args.get('items')
    if not isinstance(items, list) or not items:
        return 'items must be a non-empty list.'
    if len(items) > MAX_BATCH_ITEMS:
        return f'Too many items: {len(items)} exceeds the maximum of {MAX_BATCH_ITEMS} per request.'
    return None


def _run_batch(single_executor, user, items):
    """Run one single-item executor over each item, independently.

    Each item goes through the same endpoint (and so the same permission and
    throttle checks) as a single action would. One item's failure does not
    stop or roll back the others -- each was already its own atomic write at
    the endpoint level, so the batch is a sequence of independent operations,
    not one transaction.
    """
    from rest_framework.response import Response

    results = []
    for item in items:
        try:
            resp = single_executor(user, item)
            results.append({
                'ok': resp.status_code < 400,
                'status_code': resp.status_code,
                'data': getattr(resp, 'data', None),
                'args': item,
            })
        except Exception as exc:
            logger.error("AI batch item failed: %s", exc)
            results.append({'ok': False, 'error': str(exc), 'args': item})

    succeeded = sum(1 for r in results if r['ok'])
    return Response(
        {'results': results, 'succeeded': succeeded, 'failed': len(results) - succeeded},
        status=200 if succeeded else 400,
    )


def _execute_add_stock_batch(user, args):
    return _run_batch(_execute_add_stock, user, args.get('items') or [])


def _execute_update_stock_batch(user, args):
    return _run_batch(_execute_update_stock, user, args.get('items') or [])


def _execute_delete_stock_batch(user, args):
    items = [item if isinstance(item, dict) else {'id': item} for item in (args.get('items') or [])]
    return _run_batch(_execute_delete_stock, user, items)


WRITE_EXECUTORS = {
    'add_stock': _execute_add_stock,
    'update_stock': _execute_update_stock,
    'delete_stock': _execute_delete_stock,
    'add_stock_batch': _execute_add_stock_batch,
    'update_stock_batch': _execute_update_stock_batch,
    'delete_stock_batch': _execute_delete_stock_batch,
}

READ_TOOLS = {
    'search_stock': search_stock,
    'low_stock': low_stock,
    'sales_summary': sales_summary,
    'search_accessories': search_accessories,
}


def describe_action(name, args):
    """Plain-language summary for the confirmation dialog.

    The person confirming has to be able to tell what will happen without
    reading JSON. A vague summary makes the confirmation step theatre.
    """
    if name == 'add_stock':
        return (f"Add new stock item \"{args.get('product_name')}\" — "
                f"quantity {args.get('quantity')}, "
                f"selling price {args.get('selling_price')}"
                + (f", buying price {args['buying_price']}" if args.get('buying_price') else ""))
    if name == 'update_stock':
        changes = ', '.join(
            f"{k} to {v}" for k, v in args.items() if k != 'id' and v is not None
        )
        return f"Update stock item #{args.get('id')} — set {changes}"
    if name == 'delete_stock':
        return f"Delete stock item #{args.get('id')} permanently"
    if name == 'add_stock_batch':
        items = args.get('items') or []
        names = ', '.join(f'"{i.get("product_name")}" (qty {i.get("quantity")})' for i in items[:5])
        more = f" and {len(items) - 5} more" if len(items) > 5 else ""
        return f"Add {len(items)} new stock items: {names}{more}"
    if name == 'update_stock_batch':
        items = args.get('items') or []
        ids = ', '.join(f"#{i.get('id')}" for i in items[:10])
        more = f" and {len(items) - 10} more" if len(items) > 10 else ""
        return f"Update {len(items)} stock items: {ids}{more}"
    if name == 'delete_stock_batch':
        items = args.get('items') or []
        ids = ', '.join(
            f"#{i.get('id') if isinstance(i, dict) else i}" for i in items[:10]
        )
        more = f" and {len(items) - 10} more" if len(items) > 10 else ""
        return f"Delete {len(items)} stock items permanently: {ids}{more}"
    return f"{name} with {args}"


TOOL_SCHEMAS = [
    {
        'type': 'function',
        'function': {
            'name': 'search_stock',
            'description': 'Search shop stock (phone screens) by product name. Returns quantity and prices.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string', 'description': 'Part of the product name. Omit to list everything.'},
                    'limit': {'type': 'integer', 'description': 'Maximum items to return, up to 50.'},
                },
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'search_accessories',
            'description': 'Search accessories by product name.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'query': {'type': 'string'},
                    'limit': {'type': 'integer'},
                },
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'low_stock',
            'description': 'List stock items at or below a quantity threshold.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'threshold': {'type': 'integer', 'description': 'Default 3.'},
                },
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'sales_summary',
            'description': (
                'Sales totals and best sellers over recent days. profit_covers_sales '
                'says how many of the sales had a recorded cost; if it is lower than '
                'sales_count, say so rather than presenting profit as complete.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'days': {'type': 'integer', 'description': 'Default 7, maximum 365.'},
                },
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'add_stock',
            'description': 'Propose adding a new stock item. Requires the user to confirm before it happens.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'product_name': {'type': 'string'},
                    'quantity': {'type': 'integer'},
                    'selling_price': {'type': 'number'},
                    'buying_price': {'type': 'number', 'description': 'Cost price, used for profit.'},
                },
                'required': ['product_name', 'quantity', 'selling_price'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'update_stock',
            'description': 'Propose changing an existing stock item. Requires the user to confirm before it happens.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'integer'},
                    'product_name': {'type': 'string'},
                    'quantity': {'type': 'integer'},
                    'selling_price': {'type': 'number'},
                    'buying_price': {'type': 'number'},
                },
                'required': ['id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'delete_stock',
            'description': 'Propose deleting a stock item. Requires the user to confirm before it happens.',
            'parameters': {
                'type': 'object',
                'properties': {'id': {'type': 'integer'}},
                'required': ['id'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'add_stock_batch',
            'description': (
                f'Propose adding several new stock items at once (up to {MAX_BATCH_ITEMS}). '
                'Use this instead of calling add_stock multiple times when the user asks to add '
                'more than one item. Requires the user to confirm before it happens.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'items': {
                        'type': 'array',
                        'maxItems': MAX_BATCH_ITEMS,
                        'items': {
                            'type': 'object',
                            'properties': {
                                'product_name': {'type': 'string'},
                                'quantity': {'type': 'integer'},
                                'selling_price': {'type': 'number'},
                                'buying_price': {'type': 'number'},
                            },
                            'required': ['product_name', 'quantity', 'selling_price'],
                        },
                    },
                },
                'required': ['items'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'update_stock_batch',
            'description': (
                f'Propose changing several existing stock items at once (up to {MAX_BATCH_ITEMS}). '
                'Use this instead of calling update_stock multiple times when the user asks to update '
                'more than one item. Requires the user to confirm before it happens.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'items': {
                        'type': 'array',
                        'maxItems': MAX_BATCH_ITEMS,
                        'items': {
                            'type': 'object',
                            'properties': {
                                'id': {'type': 'integer'},
                                'product_name': {'type': 'string'},
                                'quantity': {'type': 'integer'},
                                'selling_price': {'type': 'number'},
                                'buying_price': {'type': 'number'},
                            },
                            'required': ['id'],
                        },
                    },
                },
                'required': ['items'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'delete_stock_batch',
            'description': (
                f'Propose deleting several stock items at once (up to {MAX_BATCH_ITEMS}). '
                'Use this instead of calling delete_stock multiple times when the user asks to delete '
                'more than one item. Requires the user to confirm before it happens.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'items': {
                        'type': 'array',
                        'maxItems': MAX_BATCH_ITEMS,
                        'items': {'type': 'integer'},
                        'description': 'Stock item ids to delete.',
                    },
                },
                'required': ['items'],
            },
        },
    },
]

import base64
import os
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO

import resend
from django.utils import timezone
from asgiref.sync import sync_to_async
from django.core.exceptions import ObjectDoesNotExist
from django_ratelimit.decorators import ratelimit
from dotenv import load_dotenv
from django.core.cache import cache
import asyncio
import time
from django.template.loader import render_to_string
from django.db import transaction as django_transaction
from django.db.models import DecimalField, Sum, F
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from Alltechmanagement.permissions import IsEmployeeOrManager, IsMachineClient, IsManager
from Alltechmanagement.push import notify_managers
from Alltechmanagement.search import search_products
from rest_framework.response import Response
from Alltechmanagement.GPTAgent import run_conversation
from Alltechmanagement.admin_apis import invalidate_dashboard_caches
from Alltechmanagement.celery_jwt import CeleryJWTAuthentication
from Alltechmanagement.customPagination import CustomPagination, StandardResultsSetPagination
from Alltechmanagement.models import Accessory, Customer, Insight, Sale, Stock
from django.shortcuts import render
from Alltechmanagement.serializers import (
    AccessorySerializer,
    CustomerSerializer,
    SaleSerializer,
    SellSerializer,
    StockSerializer,
)
from Alltechmanagement.throttles import InventoryCheckThrottle, SalesOperationsThrottle, InventoryModificationThrottle, \
    OrderManagementThrottle, WeeklyEmailAPIThrottle
import logging
from xhtml2pdf import pisa
load_dotenv()
logger = logging.getLogger('django')


#Custom Decorator for async api views
def async_api_view(methods, permissions=None):
    """api_view for coroutine handlers.

    `permissions` defaults to IsEmployeeOrManager rather than IsAuthenticated:
    this decorator previously hardcoded IsAuthenticated, so an async endpoint
    had no way to state a role and every one of them was open to any
    authenticated caller.
    """
    def decorator(func):
        @api_view(methods)
        @permission_classes(permissions or [IsEmployeeOrManager])
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            return asyncio.run(func(request, *args, **kwargs))

        return wrapper

    return decorator


@ratelimit(key='ip', rate='5/h')
def landing(request):
    return render(request, 'landing.html')


#Function to show time take to process a db query
def log_db_queries(f):
    from django.db import connection
    def new_f(*args, **kwargs):
        start_time = time.time()
        res = f(*args, **kwargs)
        print("\n\n")
        print("-" * 80)
        print("db queries log for %s:\n" % f.__name__)
        print(" TOTAL COUNT : % s " % len(connection.queries))
        for q in connection.queries:
            print("%s: %s\n" % (q["time"], q["sql"]))
        end_time = time.time()
        duration = end_time - start_time
        print('\n Total time: {:.3f} ms'.format(duration * 1000.0))
        print("-" * 80)
        return res

    return new_f



async def invalidate_stock_cache(item_id=None):
    """Clear the cached stock list, and one item if named.

    Both matter. The list backs the stock screen; SHOP_STOCK_<id> backs the
    single-item lookup that the sell sheet reads to decide whether there is
    enough to sell. Clearing only the list leaves the item stale, so a refund
    returned units to the database while the API went on reporting the
    pre-refund figure -- and the counter would refuse a sale it could make.
    """
    keys = ['SHOP_STOCK']
    if item_id is not None:
        keys.append(f'SHOP_STOCK_{item_id}')
    for key in keys:
        try:
            await cache.adelete(key)
        except Exception as exc:
            logging.error("Could not clear cache key %s: %s", key, exc)

def record_customer_spend(customer_name, amount):
    """Add an amount to a customer's running total.

    F() rather than read-modify-write: two tills completing sales for the same
    customer at the same moment would otherwise lose one of the amounts.
    """
    customer, created = Customer.objects.get_or_create(
        name=customer_name, defaults={'total_spent': amount},
    )
    if not created:
        Customer.objects.filter(pk=customer.pk).update(
            total_spent=F('total_spent') + amount
        )
    return customer

@api_view(['GET'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryCheckThrottle])
def get_shop2_stock(request):
    """Shop stock, optionally filtered by `q`.

    Search is done here rather than in the browser because the list is
    paginated: filtering client-side only ever searches the page that happens
    to be loaded, which looks like "we do not stock that" when the item is on
    page two.

    A search is not cached. The cache holds the unfiltered first page, which is
    what the counter opens on; caching every distinct query would fill Redis
    with single-use entries.
    """
    query = (request.GET.get('q') or '').strip()

    if query:
        queryset = search_products(Stock.objects.all(), query)
    else:
        cache_key = 'SHOP_STOCK'
        queryset = cache.get(cache_key)
        if queryset is None:
            queryset = Stock.objects.all()
            cache.set(cache_key, queryset, timeout=60 * 120)

    paginator = CustomPagination()
    paginated_queryset = paginator.paginate_queryset(queryset, request)
    serializer = StockSerializer(paginated_queryset, many=True)
    return paginator.get_paginated_response(serializer.data)


@api_view(['GET'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryCheckThrottle])
def get_shop2_stock_api(request, id):
    cache_key = f'SHOP_STOCK_{id}'
    cached_data = cache.get(cache_key)
    if cached_data is None:
        try:
            data = Stock.objects.get(pk=id)
        except Stock.DoesNotExist:
            return Response({'error': 'Item not found'}, status=404)
        serializer = StockSerializer(instance=data)
        cached_data = serializer.data
        cache.set(cache_key, cached_data, timeout=60 * 120)
    return Response({'data': cached_data})


@async_api_view(['POST'])
@throttle_classes([SalesOperationsThrottle])
async def sell_api(request, product_id):
    # Validate serializer
    serializer = SellSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=400)

    # Two ways to sell. Holding is the default because it is what the counter
    # does most: an item is handed over and paid for afterwards. A direct sale
    # is for when the customer pays there and then, and skipping the hold step
    # saves a trip to the Orders screen for something already settled.
    complete_now = bool(request.data.get('complete'))
    customer_name = serializer.validated_data['customer_name'].strip().lower()

    try:
        # Define database operations that need to be run synchronously
        @sync_to_async
        def perform_db_operations():
            with django_transaction.atomic():
                # Get product with select_for_update to prevent race conditions
                product = (Stock.objects
                           .select_for_update()
                           .get(pk=product_id))

                quantity = serializer.validated_data['quantity']

                # Read before the F() update below, so the value recorded on the
                # sale is the cost of the item as it stands right now.
                buying_price_at_sale = product.buying_price

                # Validate quantity
                if product.quantity < quantity:
                    raise ValueError('Insufficient stock')

                # Update product quantity using F() to prevent race conditions
                product.quantity = F('quantity') - quantity
                product.save()

                # Create saved transaction
                saved_transaction = Sale.objects.create(
                    product_name=serializer.validated_data['product_name'],
                    selling_price=serializer.validated_data['price'],
                    # Joining Stock at report time instead would silently
                    # rewrite historical profit on every restock.
                    buying_price=buying_price_at_sale,
                    quantity=quantity,
                    customer_name=customer_name,
                    stock=product,
                    status=(
                        Sale.Status.COMPLETED if complete_now else Sale.Status.PENDING
                    ),
                    completed_at=timezone.now() if complete_now else None,
                )

                # A direct sale is money the moment it is made, so the
                # customer's total and the dashboard have to move now rather
                # than waiting for a completion step that will never come.
                if complete_now:
                    record_customer_spend(customer_name, saved_transaction.total_amount)

                # Refresh product to get actual quantity
                product.refresh_from_db()

                return product, saved_transaction

        # Execute database operations
        try:
            product, saved_transaction = await perform_db_operations()
        except ValueError as e:
            logging.error("A value error occurred: %s", str(e))
            return Response(
                {'error': 'A value error has occurred!'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Prepare response data
        response_data = {
            'data': serializer.data,
            'transaction_id': saved_transaction.id,
            'status': saved_transaction.status,
        }

        if complete_now:
            invalidate_dashboard_caches()

        # Handle non-critical async operations
        async def async_operations():
            await invalidate_stock_cache(product_id)

        # Create background task for async operations

        await asyncio.create_task(async_operations())

        return Response(response_data, status=status.HTTP_200_OK)

    except Stock.DoesNotExist:
        return Response(
            {'error': 'Product not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        logging.error("An error occurred: %s", str(e))
        return Response(
            {'error': 'An internal error has occurred!'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@throttle_classes([InventoryCheckThrottle])
@permission_classes([IsEmployeeOrManager])
def get_saved2(request):
    data = Sale.objects.filter(status=Sale.Status.PENDING).order_by('-created_at')
    serializer = SaleSerializer(instance=data, many=True)
    return Response({'data': serializer.data})


@api_view(['POST'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([SalesOperationsThrottle])
def complete_transaction2_api(request, transaction_id):
    with django_transaction.atomic():
        try:
            sale = (Sale.objects
                    .select_for_update()
                    .get(pk=transaction_id, status=Sale.Status.PENDING))
        except Sale.DoesNotExist:
            return Response({'error': 'Pending transaction not found'}, status=404)

        customer_name = sale.customer_name.lower()
        record_customer_spend(customer_name, sale.total_amount)

        # One row, one status change. Previously this wrote copies into
        # COMPLETED_TRANSACTIONS2_FIX and RECEIPTS2_FIX and deleted the
        # original, which made the permanent record a side effect of a
        # duplicate write.
        sale.status = Sale.Status.COMPLETED
        sale.completed_at = timezone.now()
        sale.customer_name = customer_name
        sale.save(update_fields=['status', 'completed_at', 'customer_name', 'updated_at'])

        invalidate_dashboard_caches()

        return Response('Completed transaction', status=200)
@async_api_view(['POST'])
@throttle_classes([InventoryModificationThrottle])
async def add_stock2_api(request):
    if request.method == 'POST':
        data = request.data
        serializer = StockSerializer(data=data)

        @sync_to_async
        def validate_and_save():
            # raise_exception=False on purpose. Raising here throws a DRF
            # ValidationError out of sync_to_async and into the catch-all
            # below, which turns "you forgot the buying price" into a 500 and
            # tells the person nothing about what to fix.
            if not serializer.is_valid():
                return None, None
            with django_transaction.atomic():
                instance = serializer.save()
                return instance, serializer.data

        try:
            instance, serializer_data = await validate_and_save()
            if not instance:
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            # Handle non-critical operations
            async def async_operations():
                await invalidate_stock_cache(serializer_data.get('id'))

            # Create background task
            await asyncio.create_task(async_operations())

            return Response(serializer_data, status=status.HTTP_200_OK)

        except Exception as e:
            logging.error(f"Error in add_stock2_api: {e}", exc_info=True)
            return Response(
                {'error': 'An internal error has occurred.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    return Response(
        {'error': 'Invalid request method'},
        status=status.HTTP_400_BAD_REQUEST
    )


@async_api_view(['DELETE'])
@throttle_classes([InventoryModificationThrottle])
async def delete_stock2_api(request, id):
    try:
        @sync_to_async
        def delete_from_db():
            with django_transaction.atomic():
                data = Stock.objects.get(pk=id)
                data_copy = {
                    'id': data.id,
                    'product_name': data.product_name,
                    'price': int(data.selling_price),
                    'quantity': data.quantity
                }
                data.delete()
                return data_copy

        # Delete from database
        await delete_from_db()

        # Handle non-critical operations
        async def async_operations():
            try:
                await invalidate_stock_cache(id)
            except Exception as e:
                print(f"Error in async operations: {e}")

        # Create background task
        await asyncio.create_task(async_operations())

        return Response({'status': 'success'}, status=status.HTTP_200_OK)
    except Exception as e:
        logging.error(f"Error in delete_stock2_api: {e}", exc_info=True)
        return Response({"Error": "An internal error has occurred."})


@async_api_view(['PUT', 'PATCH'])
@throttle_classes([InventoryModificationThrottle])
async def update_stock2_api(request, id):
    @sync_to_async
    def validate_and_update():
        try:
            data = Stock.objects.get(pk=id)
            serializer = StockSerializer(instance=data, data=request.data, partial=True)
            if serializer.is_valid():
                with django_transaction.atomic():
                    instance = serializer.save()
                    return instance, serializer.data
            return None, None
        except Stock.DoesNotExist:
            return None, None

    try:
        instance, serializer_data = await validate_and_update()
        if not instance:
            return Response(
                {"error": "Object not found."},
                status=status.HTTP_404_NOT_FOUND
            )

        # Handle non-critical operations
        async def async_operations():
            try:
                await invalidate_stock_cache(id)
            except Exception as e:
                print(f"Error in async operations: {e}")

        # Create background task
        await asyncio.create_task(async_operations())

        return Response(serializer_data, status=status.HTTP_200_OK)

    except Exception as e:
        logging.error(f"Error in update_stock2_api: {e}", exc_info=True)
        return Response(
            {"error": "An internal error has occurred."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# GET is kept alongside POST only because the current POS calls this with GET.
# A state-changing GET is wrong -- it is replayable and cacheable -- and the GET
# form goes away once the rebuilt frontend uses POST.
@async_api_view(['GET', 'POST'])
@throttle_classes([OrderManagementThrottle])
async def refund2_api(request, id):
    @sync_to_async
    def process_refund():
        try:
            with django_transaction.atomic():
                sale = Sale.objects.select_for_update().get(
                    pk=id, status=Sale.Status.PENDING
                )

                # Accessories can be held now too, and they live in their own
                # table. Looking only in Stock would fail to find the item and
                # refuse the refund, leaving the units deducted from a shelf
                # they were never returned to.
                if sale.item_type == Sale.ItemType.ACCESSORY:
                    model = Accessory
                    scope = (
                        Accessory.objects.filter(pk=sale.accessory_id)
                        if sale.accessory_id
                        else Accessory.objects.filter(
                            product_name__iexact=sale.product_name)
                    )
                else:
                    model = Stock
                    scope = (
                        Stock.objects.filter(pk=sale.stock_id)
                        if sale.stock_id
                        else Stock.objects.filter(
                            product_name__iexact=sale.product_name)
                    )

                # Matched by id first, falling back to the name. The link is
                # cleared when a product is deleted, and the name is all a sale
                # keeps of an item that no longer exists.
                item = scope.select_for_update().first()

                if not item:
                    return None, 'Item not found in stock'

                # Return every unit the order held. This restored exactly one
                # unit before, so refunding a 3-unit order silently lost 2 from
                # stock. F() keeps it correct against a concurrent sale.
                model.objects.filter(pk=item.pk).update(
                    quantity=F('quantity') + sale.quantity
                )
                sale.delete()
                item.refresh_from_db()
                return item, sale.item_type, None
        except Sale.DoesNotExist:
            return None, None, 'Transaction not found'
        except Exception as e:
            logging.error(f"Error in process_refund: {str(e)}")
            return None, None, 'An internal error has occurred.'

    try:
        item, item_type, error = await process_refund()
        if error:
            return Response({'error': 'An internal error has occurred.'}, status=404)

        # Handle non-critical operations
        async def async_operations():
            if item_type == Sale.ItemType.ACCESSORY:
                # The accessories list is cached under its own key.
                from Alltechmanagement.accessories import CACHE_KEY
                try:
                    await cache.adelete(CACHE_KEY)
                except Exception as exc:
                    logging.error("Could not clear the accessory cache: %s", exc)
            else:
                # item.id, not just the list: this is the refund path, and
                # leaving the per-item entry stale is what made refunded units
                # invisible.
                await invalidate_stock_cache(item.id)

        # Create background task
        await asyncio.create_task(async_operations())

        return Response({'message': 'Refund Successful'})

    except Exception as e:
        logging.error(f"Error in refund2_api: {str(e)}")
        return Response({'error': 'An internal error has occurred.'}, status=500)


@api_view(['GET'])
@authentication_classes([CeleryJWTAuthentication])
@permission_classes([IsMachineClient])
@throttle_classes([WeeklyEmailAPIThrottle])
def send_sales2_api(request):
    # Checked up front rather than discovered as a failure three steps in.
    # Missing configuration is not a server fault, and reporting it as one
    # sends whoever is on call looking for a bug that is not there.
    missing = [
        name for name in ('RESEND_API_KEY', 'RESEND_SENDER_EMAIL', 'GMAIL_RECEIVER')
        if not os.getenv(name)
    ]
    if missing:
        logger.error("Sales report not configured; missing: %s", ', '.join(missing))
        return Response(
            {'error': f"Email is not configured. Missing: {', '.join(missing)}."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    try:
        resend.api_key = os.getenv("RESEND_API_KEY")

        # Completed sales that have not yet appeared in a report. This used
        # to be a whole table that got emptied after each send.
        transactions = Sale.objects.filter(
            status=Sale.Status.COMPLETED, reported_at__isnull=True
        )
        # Sum(selling_price) counted a 3-unit sale once, so any multi-unit sale
        # was undercounted in every report sent so far.
        total = transactions.aggregate(
            amount=Sum(F('selling_price') * F('quantity'), output_field=DecimalField())
        )['amount']

        if not transactions.exists():
            return Response('No completed transactions available.', status=404)

        # Materialise before marking as reported, so the template renders the
        # rows this report actually covers.
        transactions = list(transactions)

        # Render the HTML template
        html_content = render_to_string('completed_transactions.html', {
            'transactions': transactions,
            'total': total,
            'heading': 'Shop 2 Sales',
        })

        # Generate PDF from HTML
        pdf_file = BytesIO()
        pisa_status = pisa.CreatePDF(html_content, dest=pdf_file)

        if pisa_status.err:
            return Response('Failed to generate PDF.', status=500)

        # Encode PDF to base64 for Resend attachment
        pdf_base64 = base64.b64encode(pdf_file.getvalue()).decode('utf-8')

        # Prepare and send email with Resend
        sender_email = os.getenv('RESEND_SENDER_EMAIL')
        recipient_email = os.getenv('GMAIL_RECEIVER')

        params: resend.Emails.SendParams = {
            "from": sender_email,
            "to": [recipient_email],
            "subject": "Shop 2 Sales Report",
            "html": "<p>Attached is the completed transactions PDF report.</p>",
            "attachments": [
                {
                    "filename": "Shop2_Completed_Transactions.pdf",
                    "content": pdf_base64,
                }
            ],
        }

        email = resend.Emails.send(params)

        # Mark as reported rather than deleting. Deleting destroyed the only
        # copy of these rows apart from the duplicate that used to be written
        # into RECEIPTS2_FIX.
        Sale.objects.filter(pk__in=[t.pk for t in transactions]).update(
            reported_at=timezone.now()
        )

        return Response("Email with PDF sent successfully!")
    except Exception as e:
        # The message is logged in full on the server and a generic error
        # returned to the caller to avoid exposing internal exception details.
        logging.error("Error in send_completed_transactions_email: %s", e, exc_info=True)
        return Response(
            {'error': 'Could not send the sales report.'},
            status=status.HTTP_502_BAD_GATEWAY,
        )


'''
@api_view(['POST'])
def send_push_notification(request):
    try:
        # Fetch items with quantity less than or equal to 1
        data = Stock.objects.filter(quantity__lte=1)

        if data.exists():
            product_details = []
            for item in data:
                product_name = item.product_name
                quantity = item.quantity
                product_details.append(f"{product_name} (Only {quantity} left)")

            # Create a single notification title and body
            title = "Products Almost Out Of Stock"
            body = "The following products are almost out of stock:\n" + "\n".join(product_details)

            # Fetch unique tokens from the database
            registration_tokens = PushNotificationToken.objects.values_list('token', flat=True).distinct()

            if not registration_tokens:
                return Response({'error': 'No tokens available.'}, status=400)

            # Send the notification to each token individually
            success_count = 0
            failure_count = 0
            for token in registration_tokens:
                # Create the notification message
                message = messaging.Message(
                    notification=messaging.Notification(
                        title=title,
                        body=body,
                    ),
                    token=token,
                )

                # Send the notification
                try:
                    messaging.send(message)
                    success_count += 1
                except Exception as e:
                    print(f'Failed to send notification to {token}: {e}')
                    failure_count += 1

            # Return summary response
            return Response({'message': f'Notification sent to {success_count} devices.'})
        else:
            return Response({'message': 'No products are almost out of stock.'})
    except Exception as e:
        return Response({'error': str(e)}, status=500)'''


@api_view(['GET'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryCheckThrottle])
def detailed_low_stock(request):
    threshold = int(request.GET.get('threshold', 3))  # Default threshold is 3

    # Merged across both inventories. A low accessory is just as much a
    # restock problem as a low screen; reporting only Stock here made this
    # endpoint (and the dashboard/low-stock page built on it) understate what
    # actually needs reordering.
    stock_items = StockSerializer(
        Stock.objects.filter(quantity__lte=threshold), many=True
    ).data
    accessory_items = AccessorySerializer(
        Accessory.objects.filter(quantity__lte=threshold), many=True
    ).data

    combined = []
    for item in stock_items:
        combined.append({**item, 'item_type': 'SCREEN'})
    for item in accessory_items:
        combined.append({**item, 'item_type': 'ACCESSORY'})
    combined.sort(key=lambda i: i['quantity'])

    paginator = StandardResultsSetPagination()
    paginated = paginator.paginate_queryset(combined, request)

    return paginator.get_paginated_response(paginated)



def custom_404(request, exception):
    return render(request, '404.html', status=404)


def custom_500(request):
    return render(request, '500.html', status=500)


@api_view(['GET'])
@permission_classes([IsEmployeeOrManager])
@throttle_classes([InventoryCheckThrottle])
def get_customers(request):
    customers = Customer.objects.all()
    serializer = CustomerSerializer(customers, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@authentication_classes([CeleryJWTAuthentication])
@permission_classes([IsMachineClient])
@throttle_classes([WeeklyEmailAPIThrottle])
def get_daily_ai_insights(request):
    try:

        yesterday = datetime.now() - timedelta(days=1)
        data = Sale.objects.filter(
            status=Sale.Status.COMPLETED, created_at__date=yesterday
        )

        if data.exists():
            user_prompt = f"""
            Analyze the transactions that happened on {yesterday.strftime("%B %d, %Y")}.

            Tasks:
            - Summarize number of sales and total revenue.
            - Identify best-selling and highest revenue products.
            - Identify customers involved and a summary of total spent in the transactions.
            - Report products that are low in stock after sales.
            - Suggest improvements or immediate actions if necessary.
            Format nicely in sections with bullet points.
            """

            # If there is transaction data, run AI insights
            response_text = run_conversation(user_prompt, days=1)

            # Delivered to managers' devices rather than through a WhatsApp
            # session driven by a headless browser. A failure here is logged
            # and does not fail the report.
            insight = Insight.objects.create(
                kind=Insight.Kind.DAILY,
                title=f"Sales for {yesterday.strftime('%A %d %B')}",
                body=response_text,
                period_start=yesterday.date(),
                period_end=yesterday.date(),
            )

            # The notification body is truncated by the operating system
            # whatever we send, so it carries the report id and the app opens
            # the whole thing.
            delivered = notify_managers(
                insight.title,
                response_text[:240],
                {"kind": "daily_insight", "insight_id": str(insight.id)},
            )
            return Response(
                {
                    "message": response_text,
                    "insight_id": insight.id,
                    "delivered_to_devices": delivered,
                },
                status=status.HTTP_200_OK,
            )
        else:
            # If no data, respond gracefully
            return Response(
                {"error": "No transactions found for analysis."},
                status=status.HTTP_404_NOT_FOUND
            )
    except Exception as e:
        logging.error(f"Error in get_daily_ai_insights: {str(e)}")
        return Response(
            {"error": "An internal error has occurred."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@authentication_classes([CeleryJWTAuthentication])
@permission_classes([IsMachineClient])
@throttle_classes([WeeklyEmailAPIThrottle])
def get_weekly_ai_insights(request):
    try:
        today = timezone.now().date()
        current_week = today - timedelta(days=today.weekday())  # Monday of this week

        # Fetch transactions for the current week
        data = Sale.objects.filter(
            status=Sale.Status.COMPLETED, created_at__date__gte=current_week
        )

        if data.exists():
            # If there is transaction data, run AI insights
            user_prompt = f"""
Analyze all transaction data for this week.
Tasks:
- Provide a summary of total sales and revenue for the week.
- Identify the top 5 best-selling products and their revenue contribution.
- Identify the top 5 most involved customers and their revenue contribution.
- Highlight any new products that performed strongly midweek.
- Detect any products that declined in sales compared to the start of the week.
- Report stock levels and flag any items that are critically low due to sales trends.
- Offer 3-5 strategic recommendations for inventory management, or product focus for the upcoming week.
Output Requirements:
- Use bullet points for summaries.
- Clearly separate sections (Sales Summary, Product Trends, Stock Alerts, Recommendations).
- If possible, suggest emerging customer behavior patterns based on purchases.
Be concise but insightful.
"""
            response_text = run_conversation(user_prompt, days=7)

            insight = Insight.objects.create(
                kind=Insight.Kind.WEEKLY,
                title=f"Sales for the week of {current_week.strftime('%d %B')}",
                body=response_text,
                period_start=current_week,
                period_end=today,
            )

            delivered = notify_managers(
                insight.title,
                response_text[:240],
                {"kind": "weekly_insight", "insight_id": str(insight.id)},
            )
            return Response(
                {
                    "message": response_text,
                    "insight_id": insight.id,
                    "delivered_to_devices": delivered,
                },
                status=status.HTTP_200_OK,
            )
        else:
            # If no data, respond gracefully
            return Response(
                {"error": "No transactions found for analysis."},
                status=status.HTTP_404_NOT_FOUND
            )

    except Exception as e:
        logging.error(f"Error in get_weekly_ai_insights: {str(e)}")
        return Response({"error": "An error occurred: "}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

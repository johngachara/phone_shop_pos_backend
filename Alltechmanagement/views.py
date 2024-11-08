import os
from functools import wraps
from asgiref.sync import sync_to_async
from django_ratelimit.decorators import ratelimit
from dotenv import load_dotenv
from collections import defaultdict
from datetime import timedelta, datetime
from django.core.cache import cache
import asyncio
import time
from django.db.models.functions import ExtractHour, TruncDate, ExtractMonth, ExtractYear, Lower
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.db import transaction as django_transaction
from django.db.models import Sum, Count, F, Avg
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from Alltechmanagement.FCMManager import get_ref
from Alltechmanagement.celery_jwt import CeleryJWTAuthentication
from Alltechmanagement.customPagination import CustomPagination, StandardResultsSetPagination
from Alltechmanagement.models import SHOP2_STOCK_FIX, \
    SAVED_TRANSACTIONS2_FIX, \
    COMPLETED_TRANSACTIONS2_FIX, RECEIPTS2_FIX, PushNotificationToken
from django.http import JsonResponse
from django.shortcuts import render
from Alltechmanagement.serializers import SellSerializer, shop2_serializer, \
    saved_serializer2
from Alltechmanagement.throttles import InventoryCheckThrottle, SalesOperationsThrottle, InventoryModificationThrottle, \
    OrderManagementThrottle, WeeklyEmailAPIThrottle, POSAuthThrottle
from djangoProject15 import settings
from django.core.mail import send_mail
import meilisearch
import logging
from firebase_admin import db

load_dotenv()
ref = get_ref()
client = meilisearch.Client(os.getenv('MEILISEARCH_URL'), os.getenv('MEILISEARCH_KEY'))

# An index is where the documents are stored.
index = client.index('Shop2Stock')

logger = logging.getLogger('django')


#Custom Decorator for async api views
def async_api_view(methods):
    def decorator(func):
        @api_view(methods)
        @permission_classes([IsAuthenticated])
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
        print("db queries log for %s:\n" % (f.__name__))
        print(" TOTAL COUNT : % s " % len(connection.queries))
        for q in connection.queries:
            print("%s: %s\n" % (q["time"], q["sql"]))
        end_time = time.time()
        duration = end_time - start_time
        print('\n Total time: {:.3f} ms'.format(duration * 1000.0))
        print("-" * 80)
        return res

    return new_f


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def get_shop2_stock(request):
    cache_key = 'SHOP_STOCK'
    cached_data = cache.get(cache_key)
    if cached_data is None:
        cached_data = SHOP2_STOCK_FIX.objects.all()
        cache.set(cache_key, cached_data, timeout=60 * 120)
    pagination_class = CustomPagination
    paginator = pagination_class()
    paginated_queryset = paginator.paginate_queryset(cached_data, request)
    serializer = shop2_serializer(paginated_queryset, many=True)
    return paginator.get_paginated_response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def get_shop2_stock_api(request, id):
    cache_key = f'SHOP_STOCK_{id}'
    cached_data = cache.get(cache_key)
    if cached_data is None:
        data = SHOP2_STOCK_FIX.objects.get(pk=id)
        serializer = shop2_serializer(instance=data)
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

    try:
        # Define database operations that need to be run synchronously
        @sync_to_async
        def perform_db_operations():
            with django_transaction.atomic():
                # Get product with select_for_update to prevent race conditions
                product = (SHOP2_STOCK_FIX.objects
                           .select_for_update()
                           .get(pk=product_id))

                quantity = serializer.validated_data['quantity']

                # Validate quantity
                if product.quantity < quantity:
                    raise ValueError('Insufficient stock')

                # Update product quantity using F() to prevent race conditions
                product.quantity = F('quantity') - quantity
                product.save()

                # Create saved transaction
                saved_transaction = SAVED_TRANSACTIONS2_FIX.objects.create(
                    product_name=serializer.validated_data['product_name'],
                    selling_price=serializer.validated_data['price'],
                    quantity=quantity,
                    customer_name=serializer.validated_data['customer_name']
                )

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
            'transaction_id': saved_transaction.id
        }

        # Handle non-critical async operations
        async def async_operations():
            try:
                # Update search index
                body = {
                    'id': product_id,
                    'product_name': serializer.validated_data['product_name'],
                    'price': int(serializer.validated_data['price']),
                    'quantity': product.quantity,
                }

                index.update_documents([body])
            except Exception as e:
                print(f"Error updating index: {e}")

            # Clear cache using async cache operations
            await cache.adelete(f'SHOP_STOCK_{product_id}')
            await cache.adelete('SHOP_STOCK')

        # Create background task for async operations

        await asyncio.create_task(async_operations())

        return Response(response_data, status=status.HTTP_200_OK)

    except SHOP2_STOCK_FIX.DoesNotExist:
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
@permission_classes([IsAuthenticated])
def get_saved2(request):
    data = SAVED_TRANSACTIONS2_FIX.objects.order_by('-created_at')
    serializer = saved_serializer2(instance=data, many=True)
    return Response({'data': serializer.data})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([SalesOperationsThrottle])
def complete_transaction2_api(request, transaction_id):
    with django_transaction.atomic():
        transaction = SAVED_TRANSACTIONS2_FIX.objects.get(pk=transaction_id)
        transaction_name = transaction.product_name
        transaction_quantity = transaction.quantity
        transaction_price = transaction.selling_price
        transaction_customer = transaction.customer_name
        COMPLETED_TRANSACTIONS2_FIX.objects.create(product_name=transaction_name, selling_price=transaction_price,
                                                   quantity=transaction_quantity, customer_name=transaction_customer)
        RECEIPTS2_FIX.objects.create(product_name=transaction_name, selling_price=transaction_price,
                                     quantity=transaction_quantity, customer_name=transaction_customer)
        transaction.delete()
        return Response('Completed transaction', status=200)


@async_api_view(['POST'])
@throttle_classes([InventoryModificationThrottle])
async def add_stock2_api(request):
    if request.method == 'POST':
        data = request.data
        serializer = shop2_serializer(data=data)

        @sync_to_async
        def validate_and_save():
            if serializer.is_valid(raise_exception=True):
                with django_transaction.atomic():
                    instance = serializer.save()
                    return instance, serializer.data
            return None, None

        try:
            instance, serializer_data = await validate_and_save()
            if not instance:
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            # Prepare index data
            body = {
                'id': serializer_data['id'],
                'product_name': serializer_data['product_name'],
                'price': serializer_data['price'],
                'quantity': serializer_data['quantity'],
            }

            # Handle non-critical operations
            async def async_operations():
                try:
                    result = index.add_documents(body)
                    logger.info(result)
                    await cache.adelete('SHOP_STOCK')
                except Exception as e:
                    print(f"Error in async operations: {e}")

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
                data = SHOP2_STOCK_FIX.objects.get(pk=id)
                data_copy = {
                    'id': data.id,
                    'product_name': data.product_name,
                    'price': data.price,
                    'quantity': data.quantity
                }
                data.delete()
                return data_copy

        # Delete from database
        deleted_data = await delete_from_db()

        # Handle non-critical operations
        async def async_operations():
            try:
                index.delete_document(id)
                await cache.adelete(f'SHOP_STOCK_{id}')
                await cache.adelete('SHOP_STOCK')
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
            data = SHOP2_STOCK_FIX.objects.get(pk=id)
            serializer = shop2_serializer(instance=data, data=request.data, partial=True)
            if serializer.is_valid(raise_exception=True):
                with django_transaction.atomic():
                    instance = serializer.save()
                    return instance, serializer.data
            return None, None
        except SHOP2_STOCK_FIX.DoesNotExist:
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
                body = {
                    'id': id,
                    'product_name': serializer_data['product_name'],
                    'price': serializer_data['price'],
                    'quantity': serializer_data['quantity'],
                }
                index.update_documents([body])
                await cache.adelete(f'SHOP_STOCK_{id}')
                await cache.adelete('SHOP_STOCK')
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


@async_api_view(['GET'])
@throttle_classes([OrderManagementThrottle])
async def refund2_api(request, id):
    @sync_to_async
    def process_refund():
        try:
            with django_transaction.atomic():
                transaction = SAVED_TRANSACTIONS2_FIX.objects.get(pk=id)
                item = SHOP2_STOCK_FIX.objects.filter(
                    product_name__iexact=transaction.product_name
                ).first()

                if not item:
                    return None, 'Item not found in stock'

                item.quantity += 1
                item.save()
                transaction.delete()
                return item, None
        except SAVED_TRANSACTIONS2_FIX.DoesNotExist:
            return None, 'Transaction not found'
        except Exception as e:
            logging.error(f"Error in process_refund: {str(e)}")
            return None, 'An internal error has occurred.'

    try:
        item, error = await process_refund()
        if error:
            return Response({'error': 'An internal error has occurred.'}, status=404)

        # Handle non-critical operations
        async def async_operations():
            try:
                body = {
                    'id': item.id,
                    'product_name': item.product_name,
                    'price': int(item.price),
                    'quantity': item.quantity,
                }
                index.update_documents([body])
                await cache.adelete('SHOP_STOCK')
            except Exception as e:
                logging.error(f"Error in async operations: {e}")

        # Create background task
        await asyncio.create_task(async_operations())

        return Response({'message': 'Refund Successful'})

    except Exception as e:
        logging.error(f"Error in refund2_api: {str(e)}")
        return Response({'error': 'An internal error has occurred.'}, status=500)


@api_view(['GET'])
@authentication_classes([CeleryJWTAuthentication])
@permission_classes([IsAuthenticated])
@throttle_classes([WeeklyEmailAPIThrottle])
def send_sales2_api(request):
    try:
        data = COMPLETED_TRANSACTIONS2_FIX.objects.all()
        if data.exists():
            total = COMPLETED_TRANSACTIONS2_FIX.objects.aggregate(amount=Sum('selling_price'))['amount']
            with django_transaction.atomic():
                saved = SAVED_TRANSACTIONS2_FIX.objects.all()

                # Render the HTML email content using Django templates
                completed_transactions_html = render_to_string('completed_transactions.html', {
                    'transactions': data,
                    'total': total,
                    'heading': 'Shop 2 Sales',
                })
                # Get the plain text version of the email content
                completed_transactions_text = strip_tags(completed_transactions_html)
                sender_email = settings.EMAIL_HOST_USER
                recipient_email = os.getenv('GMAIL_RECEIVER')
                # Send the completed transactions email
                send_mail(
                    'Shop 2 Sales',
                    completed_transactions_text,
                    sender_email,
                    [recipient_email],
                    html_message=completed_transactions_html,
                )
                data.delete()
                return Response("Sale Sent Successfully")
        return Response("No items to send")
    except Exception as e:
        logging.error(f"Error in send_sales2_api: {str(e)}")
        return Response({'detail': 'An internal error has occurred.'}, status=500)


'''
@api_view(['POST'])
def send_push_notification(request):
    try:
        # Fetch items with quantity less than or equal to 1
        data = SHOP2_STOCK_FIX.objects.filter(quantity__lte=1)

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
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def admin_dashboard(request):
    # Total Sales Calculation
    total_sales = RECEIPTS2_FIX.objects.aggregate(total_sales=Sum(F('selling_price') * F('quantity')))['total_sales']

    # Top Selling Products
    top_products = RECEIPTS2_FIX.objects.values('product_name').annotate(
        total_quantity=Sum('quantity')
    ).order_by('-total_quantity')[:5]

    # Customer Analysis (case-insensitive)
    frequent_customers = RECEIPTS2_FIX.objects.annotate(
        customer_name_lower=Lower('customer_name')
    ).values('customer_name_lower').annotate(
        total_transactions=Count('id')
    ).order_by('-total_transactions')[:10]

    high_value_customers = RECEIPTS2_FIX.objects.annotate(
        customer_name_lower=Lower('customer_name')
    ).values('customer_name_lower').annotate(
        total_spend=Sum(F('selling_price') * F('quantity'))
    ).order_by('-total_spend')[:5]

    # Sales Trends (Daily for the last 30 days)
    end_date = timezone.now().date()
    start_date = end_date - timezone.timedelta(days=30)
    daily_sales = (
        RECEIPTS2_FIX.objects
        .filter(created_at__date__range=[start_date, end_date])
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(total_sales=Sum(F('selling_price') * F('quantity')))
        .order_by('day')
    )

    # Average Transaction Value
    avg_transaction_value = (
        RECEIPTS2_FIX.objects
        .values('created_at__date')
        .annotate(transaction_total=Sum(F('selling_price') * F('quantity')))
        .aggregate(avg_value=Avg('transaction_total'))
    )

    # Time-based Sales Analysis (Hour of day)
    hourly_sales = (
        RECEIPTS2_FIX.objects
        .annotate(hour=ExtractHour('created_at'))
        .values('hour')
        .annotate(total_sales=Sum(F('selling_price') * F('quantity')))
        .order_by('hour')
    )

    # Customer Retention (customers with more than one purchase, case-insensitive)
    repeat_customers = (
        RECEIPTS2_FIX.objects
        .exclude(customer_name='null')
        .annotate(customer_name_lower=Lower('customer_name'))
        .values('customer_name_lower')
        .annotate(purchase_count=Count('created_at__date', distinct=True))
        .filter(purchase_count__gt=1)
        .order_by('-purchase_count')
    )

    current_year = timezone.now().year
    monthly_sales = (
        RECEIPTS2_FIX.objects
        .filter(created_at__year=current_year)
        .annotate(
            month=ExtractMonth('created_at'),
            year=ExtractYear('created_at')
        )
        .values('year', 'month')
        .annotate(total_sales=Sum(F('selling_price') * F('quantity')))
        .order_by('year', 'month')
    )

    data = {
        'total_sales': total_sales,
        'top_products': list(top_products),
        'frequent_customers': list(frequent_customers),
        'high_value_customers': list(high_value_customers),
        'daily_sales_trend': list(daily_sales),
        'avg_transaction_value': avg_transaction_value['avg_value'],
        'hourly_sales': list(hourly_sales),
        'repeat_customers': list(repeat_customers),
        'monthly_sales_trend': list(monthly_sales),
    }

    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def detailed_sales(request):
    sales = RECEIPTS2_FIX.objects.values(
        'product_name',
        'selling_price',
        'quantity',
        'customer_name',
        'created_at'
    ).order_by('-created_at')

    paginator = StandardResultsSetPagination()
    result_page = paginator.paginate_queryset(sales, request)
    return paginator.get_paginated_response(result_page)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def detailed_products(request):
    products = RECEIPTS2_FIX.objects.values('product_name').annotate(
        total_quantity=Sum('quantity')
    ).order_by('-total_quantity')

    paginator = StandardResultsSetPagination()
    result_page = paginator.paginate_queryset(products, request)
    return paginator.get_paginated_response(result_page)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def detailed_customers(request):
    customers = RECEIPTS2_FIX.objects.values('customer_name').annotate(
        total_transactions=Count('id'),
        total_spend=Sum(F('selling_price') * F('quantity'))
    ).order_by('-total_spend')

    paginator = StandardResultsSetPagination()
    result_page = paginator.paginate_queryset(customers, request)
    return paginator.get_paginated_response(result_page)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def detailed_low_stock(request):
    threshold = int(request.GET.get('threshold', 3))  # Default threshold is 3

    # Query items with quantity less than or equal to the threshold
    queryset = SHOP2_STOCK_FIX.objects.filter(quantity__lte=threshold).order_by('quantity')

    paginator = StandardResultsSetPagination()
    paginated_queryset = paginator.paginate_queryset(queryset, request)

    serializer = shop2_serializer(paginated_queryset, many=True)

    return paginator.get_paginated_response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def admin_dashboard_shop1(request):
    # Fetch all receipts
    receipts = ref.get()

    # Total Sales Calculation
    total_sales = sum(float(receipt['price']) * receipt['quantity'] for receipt in receipts.values())

    # Top Selling Products
    product_sales = defaultdict(int)
    for receipt in receipts.values():
        product_sales[receipt['product_name']] += receipt['quantity']
    top_products = sorted(product_sales.items(), key=lambda x: x[1], reverse=True)[:5]

    # Customer Analysis (case-insensitive)
    customer_transactions = defaultdict(int)
    customer_spend = defaultdict(float)
    for receipt in receipts.values():
        customer_name = receipt['customer_name'].lower()  # Convert to lowercase
        customer_transactions[customer_name] += 1
        customer_spend[customer_name] += float(receipt.get('price', 0)) * float(receipt.get('quantity', 0))

    frequent_customers = sorted(customer_transactions.items(), key=lambda x: x[1], reverse=True)[:10]
    high_value_customers = sorted(customer_spend.items(), key=lambda x: x[1], reverse=True)[:5]

    # Sales Trends (Daily for the last 30 days)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    daily_sales = defaultdict(float)
    for receipt in receipts.values():
        receipt_date = datetime.fromtimestamp(receipt['timestamp'] / 1000)  # Convert milliseconds to seconds
        if start_date <= receipt_date <= end_date:
            daily_sales[receipt_date.date()] += float(receipt['price']) * int(receipt['quantity'])
    daily_sales = sorted(daily_sales.items())

    # Time-based Sales Analysis (Hour of day)
    hourly_sales = defaultdict(float)
    for receipt in receipts.values():
        hour = datetime.fromtimestamp(receipt['timestamp'] / 1000).hour
        hourly_sales[hour] += float(receipt['price']) * int(receipt['quantity'])
    hourly_sales = sorted(hourly_sales.items())

    # Customer Retention (customers with more than one purchase, case-insensitive)
    customer_purchase_dates = defaultdict(set)
    for receipt in receipts.values():
        customer_name = receipt['customer_name'].lower()  # Convert to lowercase
        customer_purchase_dates[customer_name].add(datetime.fromtimestamp(receipt['timestamp'] / 1000).date())
    repeat_customers = [(customer, len(dates)) for customer, dates in customer_purchase_dates.items() if len(dates) > 1]
    repeat_customers.sort(key=lambda x: x[1], reverse=True)

    # Monthly Sales Trend (for the current year)
    current_year = datetime.now().year
    monthly_sales = defaultdict(float)
    for receipt in receipts.values():
        receipt_date = datetime.fromtimestamp(receipt['timestamp'] / 1000)
        if receipt_date.year == current_year:
            monthly_sales[(receipt_date.year, receipt_date.month)] += float(receipt['price']) * int(receipt['quantity'])
    monthly_sales = sorted(monthly_sales.items())

    data = {
        'total_sales': total_sales,
        'top_products': top_products,
        'frequent_customers': frequent_customers,
        'high_value_customers': high_value_customers,
        'daily_sales_trend': daily_sales,
        'hourly_sales': hourly_sales,
        'repeat_customers': repeat_customers,
        'monthly_sales_trend': monthly_sales,
    }

    return JsonResponse(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def detailed_sales_shop1(request):
    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', 100))

    sales = ref.order_by_child('timestamp').limit_to_last(page * page_size).get()

    results = []
    for key, value in sales.items():
        results.append({
            'product_name': value['product_name'],
            'price': value['price'],
            'quantity': value['quantity'],
            'customer_name': value['customer_name'],
            'timestamp': value['timestamp']
        })

    results.reverse()  # To match the order in the original function
    paginated_results = results[(page - 1) * page_size:page * page_size]

    return JsonResponse({
        'count': len(sales),
        'next': f'/detailed_sales?page={page + 1}&page_size={page_size}' if len(
            paginated_results) == page_size else None,
        'previous': f'/detailed_sales?page={page - 1}&page_size={page_size}' if page > 1 else None,
        'results': paginated_results
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def detailed_products_shop1(request):
    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', 100))

    sales = ref.get()

    product_totals = defaultdict(int)
    for value in sales.values():
        product_totals[value['product_name']] += value['quantity']

    results = [{'product_name': k, 'total_quantity': v} for k, v in product_totals.items()]
    results.sort(key=lambda x: x['total_quantity'], reverse=True)

    paginated_results = results[(page - 1) * page_size:page * page_size]

    return JsonResponse({
        'count': len(results),
        'next': f'/detailed_products?page={page + 1}&page_size={page_size}' if len(
            paginated_results) == page_size else None,
        'previous': f'/detailed_products?page={page - 1}&page_size={page_size}' if page > 1 else None,
        'results': paginated_results
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def detailed_customers_shop1(request):
    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', 100))

    sales = ref.get()

    customer_data = defaultdict(lambda: {'total_transactions': 0, 'total_spend': 0})
    for value in sales.values():
        customer = value['customer_name']
        customer_data[customer]['total_transactions'] += 1
        customer_data[customer]['total_spend'] += int(value['price']) * int(value['quantity'])

    results = [{'customer_name': k, **v} for k, v in customer_data.items()]
    results.sort(key=lambda x: x['total_spend'], reverse=True)

    paginated_results = results[(page - 1) * page_size:page * page_size]

    return JsonResponse({
        'count': len(results),
        'next': f'/detailed_customers?page={page + 1}&page_size={page_size}' if len(
            paginated_results) == page_size else None,
        'previous': f'/detailed_customers?page={page - 1}&page_size={page_size}' if page > 1 else None,
        'results': paginated_results
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def low_stock_items(request):
    page = int(request.GET.get('page', 1))
    page_size = int(request.GET.get('page_size', 100))
    threshold = int(request.GET.get('threshold', 3))  # Define low stock threshold

    low_ref = db.reference('alltech/LCD')

    # Query all items, we'll filter for low stock later
    all_items = ref.order_by_child('quantity').get()

    results = []
    for key, value in all_items.items():
        if int(value['quantity']) <= threshold:
            results.append({
                'product_name': value['product_name'],
                'price': value['price'],
                'quantity': value['quantity'],
                'timestamp': value['timestamp']
            })

    results.sort(key=lambda x: x['quantity'])  # Sort by quantity ascending

    total_count = len(results)
    paginated_results = results[(page - 1) * page_size:page * page_size]

    return JsonResponse({
        'count': total_count,
        'next': f'/low_stock_items?page={page + 1}&page_size={page_size}&threshold={threshold}'
        if len(paginated_results) == page_size else None,
        'previous': f'/low_stock_items?page={page - 1}&page_size={page_size}&threshold={threshold}'
        if page > 1 else None,
        'results': paginated_results
    })


@api_view(['GET'])
@authentication_classes([CeleryJWTAuthentication])
@permission_classes([IsAuthenticated])
@throttle_classes([WeeklyEmailAPIThrottle])
def send_sales_shop1(request):
    try:
        # Reference to your Firebase database
        ref = db.reference('alltech/')
        completed_ref = ref.child('Complete')

        # Get completed transactions
        completed_data = completed_ref.get()
        if not completed_data:
            return Response("No completed transactions found.", status=404)

        # Calculate total
        total = sum(float(transaction['price']) for transaction in completed_data.values())

        # Render the HTML email content using Django templates
        try:
            completed_transactions_html = render_to_string('shop1_completed.html', {
                'transactions': completed_data.values(),
                'total': total,
                'heading': 'Shop 1 Sales',
            })
            completed_transactions_text = strip_tags(completed_transactions_html)
        except Exception as e:
            print(f"Error rendering email template: {e}")
            return Response("Error rendering email content.", status=500)

        sender_email = settings.EMAIL_HOST_USER
        recipient_email = os.getenv('GMAIL_RECEIVER')

        # Send the completed transactions email
        try:
            send_mail(
                'Shop 1 Sales',
                completed_transactions_text,
                sender_email,
                [recipient_email],
                html_message=completed_transactions_html,
            )
            print("Email sent successfully.")
        except Exception as e:
            print(f"Error sending email: {e}")
            return Response("Error sending email.", status=500)

        # Delete the completed transactions from Firebase
        try:
            completed_ref.delete()
            print("Completed transactions deleted from Firebase.")
        except Exception as e:
            print(f"Error deleting completed transactions from Firebase: {e}")
            return Response("Error deleting completed transactions from Firebase.", status=500)

        return Response("Sale Sent Successfully")

    except Exception as e:
        print(f"Unexpected error: {e}")
        return Response("An unexpected error occurred.", status=500)


@api_view(['GET'])
@authentication_classes([CeleryJWTAuthentication])
@permission_classes([IsAuthenticated])
@throttle_classes([WeeklyEmailAPIThrottle])
def send_accessories_shop1(request):
    try:
        # Reference to your Firebase database
        db_ref = db.reference('alltech/')
        completed_ref = db_ref.child('CompleteAccessory')

        # Get completed transactions
        completed_data = completed_ref.get()
        if not completed_data:
            return Response("No completed accessory transactions found.", status=404)

        # Calculate total
        total = sum(float(transaction['price']) for transaction in completed_data.values())

        # Render the HTML email content using Django templates
        try:
            completed_transactions_html = render_to_string('shop1_completed.html', {
                'transactions': completed_data.values(),
                'total': total,
                'heading': 'Shop 1 Accessories',
            })
            completed_transactions_text = strip_tags(completed_transactions_html)
        except Exception as e:
            print(f"Error rendering email template: {e}")
            return Response("Error rendering email content.", status=500)

        sender_email = settings.EMAIL_HOST_USER
        recipient_email = 'janekariu@gmail.com'

        # Send the completed transactions email
        try:
            send_mail(
                'Shop 1 Accessories',
                completed_transactions_text,
                sender_email,
                [recipient_email],
                html_message=completed_transactions_html,
            )
            print("Email sent successfully.")
        except Exception as e:
            print(f"Error sending email: {e}")
            return Response("Error sending email.", status=500)

        # Delete the completed transactions from Firebase
        try:
            completed_ref.delete()
            print("Completed accessory transactions deleted from Firebase.")
        except Exception as e:
            print(f"Error deleting completed accessory transactions from Firebase: {e}")
            return Response("Error deleting completed accessory transactions from Firebase.", status=500)

        return Response("Sale Sent Successfully")

    except Exception as e:
        print(f"Unexpected error: {e}")
        return Response("An unexpected error occurred.", status=500)


def custom_404(request, exception):
    return render(request, '404.html', status=404)


def custom_500(request):
    return render(request, '500.html', status=500)

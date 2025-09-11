import os
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO
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
from django.db.models import Sum, F
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from Alltechmanagement.FCMManager import get_ref
from Alltechmanagement.GPTAgent import run_conversation
from Alltechmanagement.admin_apis import invalidate_dashboard_caches
from Alltechmanagement.celery_jwt import CeleryJWTAuthentication
from Alltechmanagement.customPagination import CustomPagination, StandardResultsSetPagination
from Alltechmanagement.models import SHOP2_STOCK_FIX, \
    SAVED_TRANSACTIONS2_FIX, \
    COMPLETED_TRANSACTIONS2_FIX, RECEIPTS2_FIX, LcdCustomers
from django.shortcuts import render
from Alltechmanagement.serializers import SellSerializer, shop2_serializer, \
    saved_serializer2, LcdCustomerSerializer
from Alltechmanagement.throttles import InventoryCheckThrottle, SalesOperationsThrottle, InventoryModificationThrottle, \
    OrderManagementThrottle, WeeklyEmailAPIThrottle
from djangoProject15 import settings
from django.core.mail import  EmailMessage
import meilisearch
import logging
from xhtml2pdf import pisa
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

                response = index.update_documents([body])
                logger.info(response)
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
        # Fetch the transaction details
        transaction = SAVED_TRANSACTIONS2_FIX.objects.get(pk=transaction_id)
        transaction_name = transaction.product_name
        transaction_quantity = transaction.quantity
        transaction_price = transaction.selling_price
        transaction_customer = transaction.customer_name.lower()

        try:
            # Try to get the customer
            customer = LcdCustomers.objects.get(customer_name=transaction_customer)
            # Update the customer's total spent
            customer.total_spent += transaction_price * transaction_quantity
            customer.save()
        except ObjectDoesNotExist:
            # Customer not found, create a new customer entry
            LcdCustomers.objects.create(
                customer_name=transaction_customer,
                total_spent=transaction_price * transaction_quantity
            )

        # Create the completed transaction and receipt
        COMPLETED_TRANSACTIONS2_FIX.objects.create(
            product_name=transaction_name,
            selling_price=transaction_price,
            quantity=transaction_quantity,
            customer_name=transaction_customer
        )
        RECEIPTS2_FIX.objects.create(
            product_name=transaction_name,
            selling_price=transaction_price,
            quantity=transaction_quantity,
            customer_name=transaction_customer
        )
        # Clear dashboard caches
        invalidate_dashboard_caches()
        # Delete the saved transaction
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
        await delete_from_db()

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
        # Fetch completed transactions
        transactions = COMPLETED_TRANSACTIONS2_FIX.objects.all()
        total = COMPLETED_TRANSACTIONS2_FIX.objects.aggregate(amount=Sum('selling_price'))['amount']

        if not transactions.exists():
            return Response('No completed transactions available.', status=404)

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

        # Prepare email
        sender_email = settings.EMAIL_HOST_USER
        recipient_email = os.getenv('GMAIL_RECEIVER')
        email = EmailMessage(
            subject="Shop 2 Sales Report",
            body="Attached is the completed transactions PDF report.",
            from_email=sender_email,
            to=[recipient_email],
        )

        # Attach PDF
        email.attach('Shop2_Completed_Transactions.pdf', pdf_file.getvalue(), 'application/pdf')

        # Send email
        email.send()

        # Delete the transactions after sending the email
        transactions.delete()

        return Response("Email with PDF sent successfully!")
    except Exception as e:
        logging.error(f"Error in send_completed_transactions_email: {str(e)}")
        return Response('An internal error occurred.', status=500)


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
def detailed_low_stock(request):
    threshold = int(request.GET.get('threshold', 3))  # Default threshold is 3

    # Query items with quantity less than or equal to the threshold
    queryset = SHOP2_STOCK_FIX.objects.filter(quantity__lte=threshold).order_by('quantity')

    paginator = StandardResultsSetPagination()
    paginated_queryset = paginator.paginate_queryset(queryset, request)

    serializer = shop2_serializer(paginated_queryset, many=True)

    return paginator.get_paginated_response(serializer.data)



def custom_404(request, exception):
    return render(request, '404.html', status=404)


def custom_500(request):
    return render(request, '500.html', status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([InventoryCheckThrottle])
def get_customers(request):
    customers = LcdCustomers.objects.all()
    serializer = LcdCustomerSerializer(customers, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@authentication_classes([CeleryJWTAuthentication])
@permission_classes([IsAuthenticated])
@throttle_classes([WeeklyEmailAPIThrottle])
def get_daily_ai_insights(request):
    try:

        yesterday = datetime.now() - timedelta(days=1)
        data = RECEIPTS2_FIX.objects.filter(created_at__date=yesterday)

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
            response_text = run_conversation(user_prompt)
            return Response({"message": response_text}, status=status.HTTP_200_OK)
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
@permission_classes([IsAuthenticated])
@throttle_classes([WeeklyEmailAPIThrottle])
def get_weekly_ai_insights(request):
    try:
        today = timezone.now().date()
        current_week = today - timedelta(days=today.weekday())  # Monday of this week

        # Fetch transactions for the current week
        data = RECEIPTS2_FIX.objects.filter(created_at__date__gte=current_week)

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
            response_text = run_conversation(
               user_prompt)
            return Response({"message": response_text}, status=status.HTTP_200_OK)
        else:
            # If no data, respond gracefully
            return Response(
                {"error": "No transactions found for analysis."},
                status=status.HTTP_404_NOT_FOUND
            )

    except Exception as e:
        logging.error(f"Error in get_weekly_ai_insights: {str(e)}")
        return Response({"error": "An error occurred: "}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

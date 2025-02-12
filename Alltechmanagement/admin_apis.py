import logging

from django.core.cache import cache
from django.db.models import Sum, Count, Avg, F, Max, Min
from django.db.models.functions import (
     TruncWeek, TruncMonth,
    ExtractHour, ExtractDay, ExtractMonth, ExtractYear,

)
from rest_framework.decorators import api_view, permission_classes, throttle_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db.utils import DatabaseError

from Alltechmanagement.clerk_auth_class import ClerkAuthentication
from Alltechmanagement.models import RECEIPTS2_FIX
from Alltechmanagement.throttles import  DashBoardThrottle

logger = logging.getLogger('django')

def handle_database_errors(func):
    """Decorator for handling database-related errors"""

    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except DatabaseError as e:
            logger.error(str(e))
            return Response({
                'error': 'Database error occurred',
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except ValidationError as e:
            logger.error(str(e))
            return Response({
                'error': 'Validation error',
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(str(e))
            return Response({
                'error': 'An unexpected error occurred',
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return wrapper


@api_view(['GET'])
@authentication_classes([ClerkAuthentication])
@permission_classes([IsAuthenticated])
@handle_database_errors
@throttle_classes([DashBoardThrottle])
def main_dashboard(request):
    """Main dashboard with key business metrics and overview"""
    try:
        today = timezone.now().date()
        current_year = today.year
        cache_key = f"DASHBOARD_{current_year}"

        # Check if data is already cached
        cached_data = cache.get(cache_key)

        if cached_data is None:
            # Today's metrics (current year only)
            today_metrics = RECEIPTS2_FIX.objects.filter(
                created_at__date=today,
                created_at__year=current_year
            ).aggregate(
                sales_count=Count('id'),
                total_sales=Sum(F('selling_price') * F('quantity')),
                total_items_sold=Sum('quantity'),
                unique_customers=Count('customer_name', distinct=True)
            )

            # Initialize with zero if no data
            for key in today_metrics:
                if today_metrics[key] is None:
                    today_metrics[key] = 0

            # Compare with yesterday (current year)
            yesterday = today - timedelta(days=1)
            yesterday_metrics = RECEIPTS2_FIX.objects.filter(
                created_at__date=yesterday,
                created_at__year=current_year
            ).aggregate(
                total_sales=Sum(F('selling_price') * F('quantity'))
            )

            # Weekly comparison (current year)
            current_week_start = today - timedelta(days=today.weekday())
            last_week_start = current_week_start - timedelta(days=7)

            current_week_sales = RECEIPTS2_FIX.objects.filter(
                created_at__date__gte=current_week_start,
                created_at__year=current_year
            ).aggregate(
                total_sales=Sum(F('selling_price') * F('quantity'))
            )

            last_week_sales = RECEIPTS2_FIX.objects.filter(
                created_at__date__range=[last_week_start, current_week_start - timedelta(days=1)],
                created_at__year=current_year
            ).aggregate(
                total_sales=Sum(F('selling_price') * F('quantity'))
            )

            # All-time totals for comparison
            all_time_totals = RECEIPTS2_FIX.objects.aggregate(
                total_sales=Sum(F('selling_price') * F('quantity')),
                total_orders=Count('id'),
                total_customers=Count('customer_name', distinct=True)
            )

            # Prepare the response data
            response_data = {
                'current_year': current_year,
                'today_metrics': today_metrics,
                'yesterday_total_sales': yesterday_metrics['total_sales'] or 0,
                'current_week_sales': current_week_sales['total_sales'] or 0,
                'last_week_sales': last_week_sales['total_sales'] or 0,
                'all_time_totals': all_time_totals
            }

            # Cache the data for 1 hour (3600 seconds)
            cache.set(cache_key, response_data, timeout=3600)

            return Response(response_data)
        else:
            # Return cached data
            return Response(cached_data)

    except Exception as e:
        logger.error(str(e))
        return Response({
            'error': 'Failed to fetch dashboard data',
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@authentication_classes([ClerkAuthentication])
@permission_classes([IsAuthenticated])
@throttle_classes([DashBoardThrottle])
@handle_database_errors
def weekly_analysis(request):
    """Detailed weekly sales analysis"""
    try:
        weeks = int(request.GET.get('weeks', 8))
        if weeks <= 0 or weeks > 52:
            return Response({
                'error': 'Weeks parameter must be between 1 and 52'
            }, status=status.HTTP_400_BAD_REQUEST)

        end_date = timezone.now().date()
        start_date = end_date - timedelta(weeks=weeks)
        current_year = end_date.year
        cache_key = f"WEEKLY_ANALYSIS_{current_year}"
        cached_data = cache.get(cache_key)
        if cached_data is None:
            # Current year weekly data
            weekly_data = RECEIPTS2_FIX.objects.filter(
                created_at__date__range=[start_date, end_date],
                created_at__year=current_year
            ).annotate(
                week=TruncWeek('created_at')
            ).values('week').annotate(
                total_sales=Sum(F('selling_price') * F('quantity')),
                average_order_value=Avg(F('selling_price') * F('quantity')),
                total_orders=Count('id'),
                unique_customers=Count('customer_name', distinct=True),
                total_items=Sum('quantity'),
                busiest_day=Max('created_at__date'),
                slowest_day=Min('created_at__date')
            ).order_by('-week')

            # Historical comparison
            previous_year_data = RECEIPTS2_FIX.objects.filter(
                created_at__year=current_year - 1
            ).annotate(
                week=TruncWeek('created_at')
            ).values('week').annotate(
                total_sales=Sum(F('selling_price') * F('quantity'))
            ).order_by('week')
            response_data = {
                'current_year': current_year,
                'weekly_summary': list(weekly_data),
                'previous_year_comparison': list(previous_year_data),
            }
            cache.set(cache_key, response_data, timeout=3600)
            return Response(response_data)
        else:
            return Response(cached_data)
    except ValueError as e:
        logger.error(str(e))
        return Response({
            'error': 'Invalid parameter value',
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@throttle_classes([DashBoardThrottle])
@authentication_classes([ClerkAuthentication])
@permission_classes([IsAuthenticated])
@handle_database_errors
def monthly_analysis(request):
    """Monthly sales analysis with detailed metrics"""
    try:
        current_year = timezone.now().year
        cache_key = f'MONTHLY_{current_year}'
        monthly_data = cache.get(cache_key)

        if monthly_data is None:
            # Current year monthly data
            monthly_data = RECEIPTS2_FIX.objects.filter(
                created_at__year=current_year
            ).annotate(
                month=TruncMonth('created_at')
            ).values('month').annotate(
                total_sales=Sum(F('selling_price') * F('quantity')),
                average_order_value=Avg(F('selling_price') * F('quantity')),
                total_orders=Count('id'),
                unique_customers=Count('customer_name', distinct=True),
                total_items=Sum('quantity')
            ).order_by('-month')

            # Best-selling products per month (current year)
            best_selling_products = RECEIPTS2_FIX.objects.filter(
                created_at__year=current_year
            ).annotate(
                month=TruncMonth('created_at')
            ).values('month', 'product_name').annotate(
                total_quantity=Sum('quantity')
            ).order_by('month', '-total_quantity')

            # Historical comparison
            historical_comparison = RECEIPTS2_FIX.objects.annotate(
                year=ExtractYear('created_at'),
                month=TruncMonth('created_at')
            ).values('year', 'month').annotate(
                total_sales=Sum(F('selling_price') * F('quantity'))
            ).order_by('year', 'month')

            # Process best-selling products
            best_products_by_month = {}
            for product in best_selling_products:
                month = product['month']
                if month not in best_products_by_month:
                    best_products_by_month[month] = {
                        'product_name': product['product_name'],
                        'total_quantity': product['total_quantity']
                    }

            monthly_data_with_products = list(monthly_data)
            for month_data in monthly_data_with_products:
                month = month_data['month']
                month_data['best_selling_product'] = best_products_by_month.get(month, None)

            # Cache the data for 1 hour (3600 seconds)
            cache.set(cache_key, {
                'current_year': current_year,
                'current_year_data': monthly_data_with_products,
                'historical_comparison': list(historical_comparison)
            }, timeout=3600)

            return Response({
                'current_year': current_year,
                'current_year_data': monthly_data_with_products,
                'historical_comparison': list(historical_comparison)
            })
        else:
            # Return cached data
            return Response(monthly_data)

    except Exception as e:
        logger.error(str(e))
        return Response({
            'error': 'Failed to fetch monthly analysis',
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@authentication_classes([ClerkAuthentication])
@throttle_classes([DashBoardThrottle])
@permission_classes([IsAuthenticated])
@handle_database_errors
def yearly_analysis(request):
    """Yearly sales analysis with comparative metrics"""
    try:
        current_year = timezone.now().year
        cache_key = f'YEARLY_{current_year}'
        cached_data = cache.get(cache_key)
        if cached_data is None:
            # Current year detailed data
            current_year_data = RECEIPTS2_FIX.objects.filter(
                created_at__year=current_year
            ).aggregate(
                total_sales=Sum(F('selling_price') * F('quantity')),
                total_orders=Count('id'),
                average_order_value=Avg(F('selling_price') * F('quantity')),
                unique_customers=Count('customer_name', distinct=True),
                total_items=Sum('quantity'),
                highest_sale=Max(F('selling_price') * F('quantity')),
                average_items_per_order=Avg('quantity')
            )

            # Historical yearly data
            yearly_data = RECEIPTS2_FIX.objects.annotate(
                year=ExtractYear('created_at')
            ).values('year').annotate(
                total_sales=Sum(F('selling_price') * F('quantity')),
                total_orders=Count('id'),
                average_order_value=Avg(F('selling_price') * F('quantity')),
                unique_customers=Count('customer_name', distinct=True),
                total_items=Sum('quantity')
            ).order_by('-year')

            # Monthly breakdown for year-over-year comparison
            monthly_breakdown = RECEIPTS2_FIX.objects.annotate(
                year=ExtractYear('created_at'),
                month=ExtractMonth('created_at')
            ).values('year', 'month').annotate(
                sales=Sum(F('selling_price') * F('quantity')),
                orders=Count('id'),
                items_sold=Sum('quantity')
            ).order_by('year', 'month')
            response_data = {
                'current_year': current_year,
                'current_year_summary': current_year_data,
                'yearly_summary': list(yearly_data),
                'monthly_breakdown': list(monthly_breakdown)
            }
            cache.set(cache_key, response_data, timeout=3600)
            return Response(response_data)
        else:
            return Response(cached_data)
    except Exception as e:
        logger.error(str(e))
        return Response({
            'error': 'Failed to fetch yearly analysis',
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
@api_view(['GET'])
@authentication_classes([ClerkAuthentication])
@throttle_classes([DashBoardThrottle])
@permission_classes([IsAuthenticated])
@handle_database_errors
def customer_insights(request):
    """Comprehensive customer analysis"""
    try:
        current_year = timezone.now().year
        cache_key = f'CUSTOMER_{current_year}'
        cached_data = cache.get(cache_key)
        if cached_data is None:

            # Current year top customers
            current_year_top_customers = RECEIPTS2_FIX.objects.filter(
                created_at__year=current_year
            ).values('customer_name').annotate(
                total_spent=Sum(F('selling_price') * F('quantity')),
                purchase_count=Count('id'),
                average_order_value=Avg(F('selling_price') * F('quantity')),
                first_purchase=Min('created_at'),
                last_purchase=Max('created_at'),
                total_items=Sum('quantity')
            ).exclude(
                customer_name='null'
            ).order_by('-total_spent')[:20]

            # All-time top customers
            all_time_top_customers = RECEIPTS2_FIX.objects.values('customer_name').annotate(
                total_spent=Sum(F('selling_price') * F('quantity')),
                purchase_count=Count('id'),
                average_order_value=Avg(F('selling_price') * F('quantity')),
                first_purchase=Min('created_at'),
                last_purchase=Max('created_at'),
                total_items=Sum('quantity')
            ).exclude(
                customer_name='null'
            ).order_by('-total_spent')[:20]

            # Customer purchase frequency analysis
            frequency_analysis = RECEIPTS2_FIX.objects.filter(
                created_at__year=current_year
            ).values('customer_name').annotate(
                purchase_count=Count('created_at', distinct=True)
            ).values('purchase_count').annotate(
                customer_count=Count('customer_name')
            ).order_by('purchase_count')
            response_data = {
                'current_year': current_year,
                'current_year_top_customers': list(current_year_top_customers),
                'all_time_top_customers': list(all_time_top_customers),
                'purchase_frequency': list(frequency_analysis),
            }
            cache.set(cache_key, response_data, timeout=3600)
            return Response(response_data)
        else:
            return Response(cached_data)
    except Exception as e:
        logger.error(str(e))
        return Response({
            'error': 'Failed to fetch customer insights',
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@authentication_classes([ClerkAuthentication])
@permission_classes([IsAuthenticated])
@throttle_classes([DashBoardThrottle])
@handle_database_errors
def product_insights(request):
    """Detailed product performance analysis"""
    try:
        current_year = timezone.now().year
        cache_key = f'PRODUCT_INSIGHTS_{current_year}'
        cached_data = cache.get(cache_key)
        if cached_data is None:

            #Current year product performance
            current_year_performance = RECEIPTS2_FIX.objects.filter(
                created_at__year=current_year
            ).values('product_name').annotate(
                total_revenue=Sum(F('selling_price') * F('quantity')),
                units_sold=Sum('quantity'),
                average_price=Avg('selling_price'),
                first_sale=Min('created_at'),
                last_sale=Max('created_at'),
                unique_customers=Count('customer_name', distinct=True),
                total_orders=Count('id')
            ).order_by('-total_revenue')

            # All-time product performance
            all_time_performance = RECEIPTS2_FIX.objects.values('product_name').annotate(
                total_revenue=Sum(F('selling_price') * F('quantity')),
                units_sold=Sum('quantity'),
                average_price=Avg('selling_price'),
                first_sale=Min('created_at'),
                last_sale=Max('created_at'),
                unique_customers=Count('customer_name', distinct=True),
                total_orders=Count('id')
            ).order_by('-total_revenue')

            # Monthly trends for current year
            monthly_trends = RECEIPTS2_FIX.objects.filter(
                created_at__year=current_year
            ).annotate(
                month=TruncMonth('created_at')
            ).values('month', 'product_name').annotate(
                revenue=Sum(F('selling_price') * F('quantity')),
                units_sold=Sum('quantity'),
                average_price=Avg('selling_price')
            ).order_by('month', '-revenue')

            # Product growth comparison (current year vs previous year)
            previous_year = current_year - 1
            growth_comparison = RECEIPTS2_FIX.objects.filter(
                created_at__year__in=[current_year, previous_year]
            ).annotate(
                year=ExtractYear('created_at')
            ).values('year', 'product_name').annotate(
                total_revenue=Sum(F('selling_price') * F('quantity')),
                units_sold=Sum('quantity')
            ).order_by('product_name', 'year')
            response_data = {
                'current_year': current_year,
                'current_year_performance': list(current_year_performance),
                'all_time_performance': list(all_time_performance),
                'monthly_trends': list(monthly_trends),
                'growth_comparison': list(growth_comparison)
            }
            cache.set(cache_key, response_data, timeout=3600)
            return Response(response_data)
        else:
            return Response(cached_data)
    except Exception as e:
        logger.error(str(e))
        return Response({
            'error': 'Failed to fetch product insights',
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@authentication_classes([ClerkAuthentication])
@throttle_classes([DashBoardThrottle])
@permission_classes([IsAuthenticated])
@handle_database_errors
def sales_patterns(request):
    """Analysis of sales patterns and trends"""
    try:
        current_year = timezone.now().year
        cache_key = f'SALES_PATTERNS_{current_year}'
        cached_data = cache.get(cache_key)
        if cached_data is None:
            # Daily patterns for current year
            daily_patterns = RECEIPTS2_FIX.objects.filter(
                created_at__year=current_year
            ).annotate(
                day=ExtractDay('created_at')
            ).values('day').annotate(
                total_sales=Sum(F('selling_price') * F('quantity')),
                order_count=Count('id'),
                average_order_value=Avg(F('selling_price') * F('quantity')),
                items_sold=Sum('quantity')
            ).order_by('day')

            # Hour of day analysis for current year
            hourly_patterns = RECEIPTS2_FIX.objects.filter(
                created_at__year=current_year
            ).annotate(
                hour=ExtractHour('created_at')
            ).values('hour').annotate(
                total_sales=Sum(F('selling_price') * F('quantity')),
                order_count=Count('id'),
                average_order_value=Avg(F('selling_price') * F('quantity'))
            ).order_by('hour')

            # Day of week analysis
            day_of_week_patterns = RECEIPTS2_FIX.objects.filter(
                created_at__year=current_year
            ).annotate(
                day_of_week=ExtractDay('created_at')
            ).values('day_of_week').annotate(
                total_sales=Sum(F('selling_price') * F('quantity')),
                order_count=Count('id'),
                average_order_value=Avg(F('selling_price') * F('quantity')),
                items_sold=Sum('quantity')
            ).order_by('day_of_week')

            # Peak sales periods
            peak_sales = RECEIPTS2_FIX.objects.filter(
                created_at__year=current_year
            ).annotate(
                hour=ExtractHour('created_at'),
                day_of_week=ExtractDay('created_at')
            ).values('hour', 'day_of_week').annotate(
                total_sales=Sum(F('selling_price') * F('quantity')),
                order_count=Count('id')
            ).order_by('-total_sales')[:10]
            response_data = {
                'current_year': current_year,
                'daily_patterns': list(daily_patterns),
                'hourly_patterns': list(hourly_patterns),
                'day_of_week_patterns': list(day_of_week_patterns),
                'peak_sales_periods': list(peak_sales)
            }
            return Response(response_data)
        else:
            return Response(cached_data)
    except Exception as e:
        logger.error(str(e))
        return Response({
            'error': 'Failed to fetch sales patterns',
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# Cache invalidation function
def invalidate_dashboard_caches():
    """Invalidate all dashboard-related caches"""
    today = timezone.now().date()
    cache_keys = [
        f'DASHBOARD_{today.year}',
        f'WEEKLY_ANALYSIS_{today.year}',
        f'MONTHLY_{today.year}',
        f'YEARLY_{today.year}',
        f'CUSTOMER_{today.year}',
        f'PRODUCT_INSIGHTS_{today.year}',
        f'SALES_PATTERNS_{today.year}',

    ]
    cache.delete_many(cache_keys)
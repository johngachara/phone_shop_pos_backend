from django.urls import path
from . import views
from django.conf.urls import handler404, handler500

from .admin_apis import main_dashboard, weekly_analysis, monthly_analysis, yearly_analysis, customer_insights, \
    product_insights, sales_patterns
from .celery_auth_api import CeleryAuthTokenView
from .firebase_auth import FirebaseAuthTokenView
from .refresh_token_view import RefreshTokenView
handler404 = 'Alltechmanagement.views.custom_404'
handler500 = 'Alltechmanagement.views.custom_500'

urlpatterns = [
    path('', views.landing, name='landing'),
    path('api/firebase-auth/', FirebaseAuthTokenView.as_view(), name='firebase-auth'),
    path('api/get_shop2_stock', views.get_shop2_stock, name='get_shop2_stock_api'),
    path('api/refresh-token/',RefreshTokenView.as_view(), name='refresh-token'),
    path('api/get_shop2_stock_api/<int:id>', views.get_shop2_stock_api, name='get_shop2_stock_api'),
    path('api/sell2/<int:product_id>', views.sell_api, name='sell2api'),
    path('api/saved2', views.get_saved2, name='saved_api2'),
    path('api/complete2/<int:transaction_id>', views.complete_transaction2_api, name='complete_transaction2_api'),
    path('api/add_stock2', views.add_stock2_api, name='add_stock2_api'),
    path('api/delete_stock2_api/<int:id>', views.delete_stock2_api, name='delete_stock2_api'),
    path('api/update_stock2/<int:id>', views.update_stock2_api, name='update_stock2_api'),
    path('api/refund2/<int:id>', views.refund2_api, name='refund2_api'),
    path('api/send_sale2', views.send_sales2_api, name='send_sales2_api'),
    path('api/detailed/low_stock/', views.detailed_low_stock, name='detailed_lowstock'),
    path('api/celery-token/', CeleryAuthTokenView.as_view(), name='celery_token'),
    path('api/customers/',views.get_customers, name='get_customers'),
    path('api/dashboard/', main_dashboard, name='main-dashboard'),
    path('api/weekly/', weekly_analysis, name='weekly-analysis'),
    path('api/monthly/', monthly_analysis, name='monthly-analysis'),
    path('api/yearly/', yearly_analysis, name='yearly-analysis'),
    path('api/customers-insights/', customer_insights, name='customer-insights'),
    path('api/products-insights/', product_insights, name='product-insights'),
    path('api/patterns/', sales_patterns, name='sales-patterns'),
]

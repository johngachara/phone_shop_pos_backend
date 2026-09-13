from django.urls import path
from . import views
from django.conf.urls import handler404, handler500

from .admin_apis import main_dashboard, weekly_analysis, monthly_analysis, yearly_analysis, customer_insights, \
    product_insights, sales_patterns
from .celery_auth_api import CeleryAuthTokenView
from .accessories import (
    add_accessory,
    delete_accessory,
    get_accessory,
    list_accessories,
    sell_accessory,
    update_accessory,
)
from .ai.views import ai_chat, ai_confirm
from .health import health
from .webauthn_views import (
    authentication_options,
    list_credentials,
    registration_options,
    verify_authentication,
    verify_registration,
)
from .user_admin import UserAdminDetailView, UserAdminListView
handler404 = 'Alltechmanagement.views.custom_404'
handler500 = 'Alltechmanagement.views.custom_500'

urlpatterns = [
    path('', views.landing, name='landing'),
    path('api/health/', health, name='health'),
    path('api/get_shop2_stock', views.get_shop2_stock, name='get_shop2_stock_api'),
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
    path('api/accessories/', list_accessories, name='accessory-list'),
    path('api/accessories/add/', add_accessory, name='accessory-add'),
    path('api/accessories/<int:accessory_id>/', get_accessory, name='accessory-detail'),
    path('api/accessories/<int:accessory_id>/update/', update_accessory, name='accessory-update'),
    path('api/accessories/<int:accessory_id>/delete/', delete_accessory, name='accessory-delete'),
    path('api/accessories/<int:accessory_id>/sell/', sell_accessory, name='accessory-sell'),
    path('api/ai/chat/', ai_chat, name='ai-chat'),
    path('api/ai/confirm/', ai_confirm, name='ai-confirm'),
    path('api/passkeys/', list_credentials, name='passkey-list'),
    path('api/passkeys/register/options/', registration_options, name='passkey-register-options'),
    path('api/passkeys/register/verify/', verify_registration, name='passkey-register-verify'),
    path('api/passkeys/auth/options/', authentication_options, name='passkey-auth-options'),
    path('api/passkeys/auth/verify/', verify_authentication, name='passkey-auth-verify'),
    path('api/users/', UserAdminListView.as_view(), name='user-admin-list'),
    path('api/users/<str:user_id>/', UserAdminDetailView.as_view(), name='user-admin-detail'),
    path('api/customers/',views.get_customers, name='get_customers'),
    path('api/dashboard/', main_dashboard, name='main-dashboard'),
    path('api/weekly/', weekly_analysis, name='weekly-analysis'),
    path('api/monthly/', monthly_analysis, name='monthly-analysis'),
    path('api/yearly/', yearly_analysis, name='yearly-analysis'),
    path('api/customers-insights/', customer_insights, name='customer-insights'),
    path('api/products-insights/', product_insights, name='product-insights'),
    path('api/patterns/', sales_patterns, name='sales-patterns'),
    path('api/daily-ai/', views.get_daily_ai_insights, name='sales-patterns'),
    path('api/weekly-ai/', views.get_weekly_ai_insights, name='sales-patterns'),
]

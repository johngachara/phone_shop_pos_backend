"""
Django settings for djangoProject15 project.

Generated by 'django-admin startproject' using Django 4.2.8.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/4.2/ref/settings/
"""
import os.path
from datetime import timedelta
from pathlib import Path
from django.contrib import messages
import os
from dotenv import load_dotenv
from django.middleware.cache import UpdateCacheMiddleware, FetchFromCacheMiddleware

load_dotenv()
# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['127.0.0.1', 'localhost', os.getenv('SERVER_URL'), os.getenv('SERVER_IP')]

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'Alltechmanagement.apps.AlltechmanagementConfig',
    'crispy_forms',
    'crispy_bootstrap5',
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'csp',
    'django_extensions',
    'channels'
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    'django.middleware.security.SecurityMiddleware',
    "django_permissions_policy.PermissionsPolicyMiddleware",
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'csp.middleware.CSPMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
]
PERMISSIONS_POLICY = {
    "accelerometer": [],
    "ambient-light-sensor": [],
    "autoplay": [],
    "camera": [],
    "display-capture": [],
    "document-domain": [],
    "encrypted-media": [],
    "fullscreen": [],
    "geolocation": [],
    "gyroscope": [],
    "interest-cohort": [],
    "magnetometer": [],
    "microphone": [],
    "midi": [],
    "payment": [],
    "usb": [],
}
WHITENOISE_MAX_AGE = 31536000  # 1 year in seconds
#CORS_ALLOW_ALL_ORIGINS = True  # For development only
CORS_ALLOW_CREDENTIALS = True
CSP_DEFAULT_SRC = ("'self'",)
CSP_SCRIPT_SRC = ("'self'", "https://cdn.jsdelivr.net")
CSP_STYLE_SRC = ("'self'", "https://cdn.jsdelivr.net")
CSP_IMG_SRC = (
    "'self'", "https://cdn.jsdelivr.net", "https://cdn-icons-png.flaticon.com", "https://source.unsplash.com")
CSP_FONT_SRC = ("'self'", "https://cdn.jsdelivr.net", "https://fonts.gstatic.com")
CSP_FRAME_SRC = ("'none'",)
CSP_OBJECT_SRC = ("'none'",)
CSP_FRAME_ANCESTORS = ("'none'",)
CSP_FORM_ACTION = ("'self'",)
CSP_CONNECT_SRC = ("'self'",)
ROOT_URLCONF = 'djangoProject15.urls'
CORS_ALLOWED_ORIGINS = [
    'https://main.gachara.store',
    'https://shop1.gachara.store',
    'http://localhost:3000',

]

# HSTS settings
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = 'DENY'

SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
CORS_ALLOW_METHODS = [
    'GET',
    'POST',
    'PUT',
    'PATCH',
    'DELETE',
    'OPTIONS'
]

WHITENOISE_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_HEADERS = [
    'content-type',
    'authorization',
    'X-CSRFToken',
]
CELERY_API_KEY = os.getenv('CELERY_KEY')
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates']
        ,
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'djangoProject15.wsgi.application'

# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

RATELIMIT_IP_META_KEY = lambda request: request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0] or request.META.get('REMOTE_ADDR')


# settings.py

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_CRED'),
        'USER': os.getenv('DB_CRED'),
        'PASSWORD': os.getenv('DB_PASS'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT'),
    }
}

# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.getenv('REDIS_URL'),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "PASSWORD": os.getenv('REDIS_PASS'),
        },
        "KEY_PREFIX": "alltech_mgmt"
    }
}
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'Alltechmanagement.custom_auth.CustomJWTAuthentication',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',  # Only allow JSON responses
    ],

    'DEFAULT_THROTTLE_RATES': {

        'anon': '2/hour',
        'user': '4/minute',
        'pos_auth': '5/minute',
        'inventory_mod': '30/minute',
        'sales_ops': '120/minute',
        'order_mgmt': '60/minute',
        'inventory_check': '300/minute',
        'celery_auth_token': '5/day',
        'weekly_email': '5/day',

    },
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
    ],
    'NUM_PROXIES': 1,
}

# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Africa/Nairobi'

USE_I18N = True

USE_TZ = True

# settings.py


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'Alltechmanagement/static')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
'''STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "Alltechmanagement/static"),
]'''
# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field
#DJANGO_ALLOW_ASYNC_UNSAFE = True  # Only in development
ASGI_APPLICATION = 'djangoProject15.asgi.application'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
}
KEY = os.path.join(BASE_DIR, 'key_pair.json')
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'django_file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': 'django.log',
            'formatter': 'verbose',
        },
        'scheduler_file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': 'scheduler.log',
            'formatter': 'verbose',
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['django_file', 'console'],
            'level': 'INFO',
            'propagate': True,
        },
        'scheduler': {
            'handlers': ['scheduler_file', 'console'],
            'level': 'DEBUG',
            'propagate': True,
        },
    },
}

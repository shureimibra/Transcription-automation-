import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-antigravity-transcription-platform-secret-key-development'

DEBUG = True

ALLOWED_HOSTS = ['*']

# Dynamic dependency check
try:
    import daphne
    import channels
    HAS_CHANNELS = True
except ImportError:
    HAS_CHANNELS = False

try:
    import celery
    HAS_CELERY = True
except ImportError:
    HAS_CELERY = False

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'apps.transcriptions',
]

if HAS_CHANNELS:
    INSTALLED_APPS.insert(0, 'daphne')
    INSTALLED_APPS.insert(1, 'channels')
    ASGI_APPLICATION = 'transcription_proj.asgi.application'
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }
else:
    WSGI_APPLICATION = 'transcription_proj.wsgi.application'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    # Enable CORS headers if django-cors-headers is available
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Check if django-cors-headers is installed and configure it
try:
    import corsheaders
    INSTALLED_APPS.append('corsheaders')
    MIDDLEWARE.insert(2, 'corsheaders.middleware.CorsMiddleware')
    CORS_ALLOW_ALL_ORIGINS = True
except ImportError:
    pass

ROOT_URLCONF = 'transcription_proj.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
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

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = []

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Celery Configuration
if HAS_CELERY:
    CELERY_BROKER_URL = 'sqla+sqlite:///celerydb.sqlite'
    CELERY_RESULT_BACKEND = 'db+sqlite:///results.sqlite'
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_ACCEPT_CONTENT = ['json']
    CELERY_TASK_SERIALIZER = 'json'
    CELERY_RESULT_SERIALIZER = 'json'
    CELERY_TIMEZONE = 'UTC'

# REST Framework settings (enable full permission layout access for dev simplicity)
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [],
}

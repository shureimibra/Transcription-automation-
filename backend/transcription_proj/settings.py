import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """
    Minimal KEY=VALUE loader for backend/.env, applied before anything
    else reads os.environ. Exists because Windows `setx` only writes the
    user registry -- it does NOT update already-running processes, and
    new processes only inherit the change once their ultimate parent
    (usually explorer.exe) has itself refreshed, which in practice means
    a logoff/logon, not just "open a new terminal window". A local .env
    file sidesteps that OS/session propagation entirely: every entry
    point (manage.py, wsgi.py, asgi.py, celery.py) goes through
    django.setup() -> this settings module, so this is the one place
    that guarantees the value is visible everywhere, immediately.
    Real OS environment variables still win if already set (checked via
    setdefault), so this never overrides an intentional override.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / '.env')

def _flag(name: str, default: bool) -> bool:
    """An environment variable read as a boolean, not as a truthy string.

    `bool(os.environ.get('DEBUG'))` is True for the string "False", which is
    the kind of mistake that ships a debug console to the internet.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


# WHICH SIDE OF THE FENCE THIS PROCESS IS ON.
#
# One switch, defaulting to PRODUCTION. Every unsafe convenience below is an
# opt-in from here, so forgetting to set something yields a locked-down
# server rather than an open one -- the failure that costs nothing instead
# of the one that leaks everything. Local work sets DJANGO_ENV=development
# in .env, which is already gitignored.
DJANGO_ENV = os.environ.get('DJANGO_ENV', 'production').strip().lower()
DEVELOPMENT = DJANGO_ENV in ('dev', 'development', 'local', 'test')

# THE KEY THAT SIGNS SESSIONS AND TOKENS.
#
# A hardcoded key means anyone holding this source can forge a session for
# any account, so in production it MUST come from the environment and the
# process refuses to start without it. Development keeps a fixed literal so
# that a fresh checkout runs and logins survive a restart.
if DEVELOPMENT:
    SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY') or (
        'django-insecure-antigravity-transcription-platform-development-only')
else:
    SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
    # 50 CHARACTERS IS DJANGO'S OWN THRESHOLD (security.W009). Refusing here
    # rather than warning later means a short key cannot reach production
    # and be discovered by a check nobody ran.
    if (not SECRET_KEY or SECRET_KEY.startswith('django-insecure-')
            or len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5):
        raise RuntimeError(
            'DJANGO_SECRET_KEY is missing or is the development key. '
            'Generate one and put it in the 0600 .env: '
            '    python -c "from django.core.management.utils import '
            'get_random_secret_key as k; print(k())"'
        )

DEBUG = _flag('DJANGO_DEBUG', default=DEVELOPMENT)

# DEBUG=True SERVES A STACK TRACE, THE SETTINGS AND THE SQL to whoever asks.
# Paired with ALLOWED_HOSTS=['*'] that is every visitor, so the two are
# decided together rather than in separate places that can disagree.
if DEVELOPMENT:
    ALLOWED_HOSTS = ['*']
else:
    ALLOWED_HOSTS = [h.strip() for h in
                     os.environ.get('DJANGO_ALLOWED_HOSTS', '').split(',')
                     if h.strip()]
    if not ALLOWED_HOSTS:
        raise RuntimeError(
            'DJANGO_ALLOWED_HOSTS is required in production, e.g. '
            'DJANGO_ALLOWED_HOSTS=app.example.com,203.0.113.10')
    if DEBUG:
        raise RuntimeError(
            'DJANGO_DEBUG is on outside development. That serves a stack '
            'trace, the settings and the SQL to every visitor.')

# ---------------------------------------------------------------------------
# Transport security. Off in development, where there is no certificate.
# ---------------------------------------------------------------------------
if not DEVELOPMENT:
    SECURE_SSL_REDIRECT = _flag('DJANGO_SSL_REDIRECT', True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'same-origin'
    # Behind nginx or any reverse proxy, Django sees plain HTTP and would
    # redirect for ever without being told how the proxy reports the scheme.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    # HSTS STARTS AT ZERO ON PURPOSE. Turning it on tells every browser to
    # refuse plain HTTP to this host for the duration, and that instruction
    # cannot be withdrawn early -- a certificate problem then locks everyone
    # out for the full period. Raise it deliberately once TLS is proven.
    SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_HSTS_SECONDS', '0'))
    if SECURE_HSTS_SECONDS:
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True
        SECURE_HSTS_PRELOAD = True

# ---------------------------------------------------------------------------
# CSRF trusted origins
#
# WHY THIS IS NEEDED EVEN THOUGH THE API IS TOKEN-AUTHENTICATED. Django checks
# the `Origin` header against `request.get_host()` on every unsafe request that
# reaches its CSRF machinery. In development the browser's origin is the Vite
# dev server (:3000) while the request arrives at Django (:8002) -- and Vite's
# `changeOrigin: true` rewrites the Host header, so the two can never match on
# their own. Any request that falls back to session authentication therefore
# fails with "Origin checking failed", which is what a worker sees the moment
# their token header is missing for any reason.
#
# `localhost` and `127.0.0.1` are BOTH listed on purpose: they are different
# origins to a browser, with separate cookies and separate localStorage, and
# whichever one a person happens to type has to work.
#
# Set CSRF_TRUSTED_ORIGINS in the environment (comma-separated) for anything
# beyond local development -- a tunnel host or a real domain.
_default_csrf_origins = [
    'http://localhost:3000', 'http://127.0.0.1:3000',
    'http://localhost:3001', 'http://127.0.0.1:3001',
    'http://localhost:8002', 'http://127.0.0.1:8002',
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',')
    if origin.strip()
] or _default_csrf_origins

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
    'rest_framework.authtoken',
    'apps.transcriptions',
    'apps.workforce',
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

    # ALLOWING EVERY ORIGIN IS NOT A CORS SETTING HERE, IT IS AN AUTH HOLE.
    #
    # This API authenticates with a session cookie, so "any origin may call
    # us with credentials" means any page a signed-in tasker visits can act
    # as them: claim their tasks, read transcripts, submit work. In
    # development the front end is on another port and the wildcard is the
    # simplest way to let it through; in production the origins are named.
    if DEVELOPMENT:
        CORS_ALLOW_ALL_ORIGINS = True
    else:
        CORS_ALLOW_ALL_ORIGINS = False
        CORS_ALLOW_CREDENTIALS = True
        CORS_ALLOWED_ORIGINS = [
            o.strip() for o in
            os.environ.get('DJANGO_CORS_ORIGINS', '').split(',') if o.strip()
        ]
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

# ---------------------------------------------------------------------------
# Database
#
# PostgreSQL is the production database as of Phase 2; SQLite remains
# available for local development via DB_ENGINE=sqlite.
#
# The switch is deliberate rather than automatic. Row-level locking
# (select_for_update), which the task-claiming logic depends on to stop two
# workers taking the same task, is a NO-OP on SQLite -- Django accepts the
# call and silently does nothing. A test suite run on SQLite therefore
# reports concurrency guarantees it has not actually verified, which is
# worse than not testing them. See apps.workforce.tests.
# ---------------------------------------------------------------------------
DB_ENGINE = os.environ.get('DB_ENGINE', 'sqlite').strip().lower()

if DB_ENGINE in ('postgres', 'postgresql'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('POSTGRES_DB', 'transcription_platform'),
            'USER': os.environ.get('POSTGRES_USER', 'transcription_app'),
            'PASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
            'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
            'PORT': os.environ.get('POSTGRES_PORT', '5432'),
            # Persistent connections. Every transcription runs on its own
            # thread and each thread opens its own connection, so without
            # reuse a busy server pays a TCP+auth handshake per pipeline
            # stage. 60s is short enough that a restarted database is picked
            # up quickly.
            'CONN_MAX_AGE': int(os.environ.get('POSTGRES_CONN_MAX_AGE', '60')),
            'CONN_HEALTH_CHECKS': True,
            'OPTIONS': {
                # Fail fast rather than hanging a worker thread forever.
                'connect_timeout': 10,
            },
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            # SQLite serializes writes; background threads (transcription,
            # series, translation, discovery pipelines) now write concurrently
            # with the request thread, and the sqlite3 default 5s busy-timeout
            # can occasionally be too short under that contention, surfacing as
            # an intermittent "database is locked" error. 20s gives concurrent
            # writers enough room to queue up instead of failing outright.
            'OPTIONS': {'timeout': 20},
        }
    }

# Password validation
# Real accounts exist from Phase 2 onward, so an empty validator list is no
# longer defensible: it would let an administrator set a worker's password to
# "1234". These are Django's stock validators, which is deliberate -- a
# hand-rolled policy would be weaker and is not the kind of thing worth
# inventing.
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

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
# RUNNING THE TEST SUITE IS NOT AN ATTACK.
#
# Throttling keys on the client address, and every test shares one. A suite
# that logs in a few hundred times therefore trips the login limit and
# starts failing tests that have nothing to do with rate limiting -- and
# the failures move around as tests are added, which is the worst kind.
#
# Turned OFF for tests, and covered by its own test file instead, where the
# rates are set deliberately. Disabling a protection in tests is only
# acceptable if the protection is proved somewhere else.
_RUNNING_TESTS = 'test' in sys.argv or bool(os.environ.get('PYTEST_CURRENT_TEST'))

#: THE DEPLOYED RATES, kept as their own name so they can be read and
#: asserted on even when the suite runs with throttling switched off. A
#: number nobody can inspect is a number nobody maintains.
THROTTLE_RATES = {
    'anon': os.environ.get('THROTTLE_ANON', '60/min'),
    'user': os.environ.get('THROTTLE_USER', '600/min'),
    # RAISED FROM 10 BECAUSE THE KEY IS AN IP ADDRESS, NOT A PERSON.
    # Six taskers behind one office NAT share this budget, so a rate tuned
    # for one person locks out their colleagues -- and the symptom, "I
    # cannot sign in", looks nothing like the cause. Still far below
    # anything useful for working through a wordlist.
    'login': os.environ.get('THROTTLE_LOGIN', '30/min'),
    # The ones that cost money rather than CPU. A worker reviewing a task
    # touches these a handful of times an hour; anything near these numbers
    # is a loop.
    'ai': os.environ.get('THROTTLE_AI', '30/hour'),
    'media': os.environ.get('THROTTLE_MEDIA', '120/hour'),
    'submit': os.environ.get('THROTTLE_SUBMIT', '60/hour'),
}

REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [],

    # RATE LIMITING, BECAUSE THE EXPENSIVE ENDPOINTS SPEND REAL MONEY.
    #
    # Transcription, translation, AI revision and media discovery each cost
    # provider quota or Eagle minutes. With no throttle at all, one script
    # -- or one enthusiastic retry loop in a browser tab -- can drain a
    # day's Gemini allowance before anybody notices, and the first symptom
    # is warming quietly failing for everyone.
    #
    # The rates are deliberately generous for six people doing real work and
    # ruinous for a loop. 'anon' is tight because an unauthenticated caller
    # has no business here beyond logging in.
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.ScopedRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': THROTTLE_RATES,

    # NO BROWSABLE API OUTSIDE DEVELOPMENT. DRF's own default renderer list
    # includes BrowsableAPIRenderer unconditionally -- not gated by DEBUG --
    # so every endpoint in this project rendered an interactive HTML
    # debugging UI, with live forms for POST/PATCH/DELETE, to anyone who
    # asked for it with `Accept: text/html`, in production exactly as in
    # development. Permission checks still apply underneath -- this never
    # granted access to data a JSON request could not also reach -- but it
    # handed out the whole API's shape and available actions to anyone
    # probing with a browser, for no benefit once the app is deployed.
    'DEFAULT_RENDERER_CLASSES': (
        [
            'rest_framework.renderers.JSONRenderer',
            'rest_framework.renderers.BrowsableAPIRenderer',
        ] if DEVELOPMENT else [
            'rest_framework.renderers.JSONRenderer',
        ]
    ),
}

if _RUNNING_TESTS:
    REST_FRAMEWORK['DEFAULT_THROTTLE_CLASSES'] = []
    REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] = {}


# Which of Eagle's two transcription rubrics our transcripts are written and
# checked against. Edited verbatim is the platform's default for the general
# transcription work we see (fillers and false starts removed, contractions
# expanded, grammar corrected); strict verbatim is the legal/court rubric,
# where every one of those corrections is itself a defect.
#
# It is a setting because getting it wrong is not a style disagreement: a
# transcript that is flawless under one standard is a guaranteed rejection
# under the other.
EAGLE_TRANSCRIPTION_STANDARD = os.environ.get(
    'EAGLE_TRANSCRIPTION_STANDARD', 'edited_verbatim')

# Which quality gate runs over the warmed transcript. 'eagle_web' is the
# zero-tolerance profile: it refuses on MEDIUM findings and will not sign
# off on a local-model fallback, which is what "ready to submit" has to mean
# when a rejection costs real money.
#
# IT ALSO COSTS THE MOST -- roughly three model requests per task against
# one. Set this to 'none' to warm without a gate when the budget matters
# more than the first-pass acceptance rate.
EAGLE_WARM_QUALITY_PROFILE = os.environ.get(
    'EAGLE_WARM_QUALITY_PROFILE', 'eagle_web')


# The name that appears on submitted work as "Transcribed By" / "Translated
# By". It is the EAGLE ACCOUNT HOLDER's name, not the tasker's: the work goes
# through that account and is graded against it, and a worker's real name has
# no business being sent to an external platform.
EAGLE_SUBMITTER_NAME = os.environ.get('EAGLE_SUBMITTER_NAME', 'Shureimi ibrahim')


# Whether warming runs the separate coach-standard pass over the ENGLISH
# source of a translation task before translating it.
#
# Off by default. It roughly doubles the provider requests a warm
# translation costs, and its English output is never submitted -- only the
# French is. The cleanup it performed is folded into the translation prompt
# instead, and the tasker's own polish step is what brings the French to
# submission standard.
#
# Turn it on if quota stops being the binding constraint.
EAGLE_WARM_STANDARD_ON_TRANSLATION = (
    os.environ.get('EAGLE_WARM_STANDARD_ON_TRANSLATION', '') == '1')


# TEMPORARY, FOR ONBOARDING A NEW COHORT OF TASKERS ONTO THE WORKFLOW.
#
# Claiming a real task spends real Eagle minutes and starts a real one-hour
# clock -- neither is something a tasker should risk while still learning
# which button does what. With this on, the two REQUIRED self-check writes
# (TranslationDraftView's polish save, TranscriptCheckPromptView's accuracy-
# check save) additionally accept a task that is unclaimed but still in the
# open, claimable pool -- the same condition every read-only view on this
# task already allows -- instead of requiring the caller to actually hold
# it. Nothing else changes: editing a segment's text already worked without
# holding (EditSegmentView/RevertSegmentView only ever required being
# logged in), and Submit-to-Eagle is deliberately NOT included here --
# sending an unclaimed task's work to Eagle is a real, external, billable
# action, not a rehearsal, and the state machine's own CLAIMABLE_STATES
# check on submit already refuses it regardless of this flag.
#
# Every write let through this way is tagged 'training_mode': True in its
# TaskEvent metadata, so it stays distinguishable from a real submission's
# audit trail. And services.claim_task clears any polished_text/
# accuracy_check_notes left behind by a practice run the FIRST time that
# task is ever really claimed, so a genuine worker never opens a task to
# find someone else's rehearsal sitting in it.
#
# Off by default. Turn this back off once the cohort has claimed real tasks
# a few times each -- it is an onboarding aid, not a permanent mode.
WORKFORCE_TRAINING_MODE = _flag('WORKFORCE_TRAINING_MODE', False)

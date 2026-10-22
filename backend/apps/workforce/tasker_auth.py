"""
Public sign-up and "Continue with Google" for outside taskers.

THE ONLY PLACE AN ACCOUNT CAN BE CREATED WITHOUT AN ADMINISTRATOR. So
everything here assumes the caller is hostile:

  * The role is fixed server-side to EAGLE_TASKER. Nothing in the request
    can choose it -- a body containing `role`, `is_staff` or anything else
    is ignored, not honoured.
  * Usernames are a short allow-listed alphabet. That is not what stops SQL
    injection (the ORM parameterises every query) -- it stops look-alike
    names, invisible characters and markup from ever reaching an admin
    screen, a log line or another tasker's view.
  * Rate limits are per client address and tight: a person signs up once.
    A honeypot field quietly turns away the simplest bots.
  * Sign-up can be switched off with one setting (TASKER_SIGNUP_OPEN) if
    it is ever abused, without touching existing accounts.

GOOGLE IS VERIFIED, NOT TRUSTED. The browser sends Google's signed ID
token; this server checks the signature, the audience (our client id), the
issuer and that Google verified the email, before believing a word of it.
"""
import hashlib
import hmac
import logging
import re
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status, views
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from . import roles
from .models import Account, EmailCode, ExternalIdentity, TokenActivity
from .perimeter import public_identity
from .views import AUTH_CLASSES

logger = logging.getLogger('workforce')
User = get_user_model()

#: Letters, digits and . _ - only; must start with a letter or digit.
USERNAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{2,29}$')

#: Names that would let an account pass itself off as staff or the system.
RESERVED = frozenset({
    'admin', 'administrator', 'root', 'support', 'help', 'staff', 'system',
    'security', 'moderator', 'owner', 'billing', 'api', 'null', 'undefined',
    'shurz', 'workdesk', 'eagle', 'coach', 'official',
})


def signup_open() -> bool:
    return str(getattr(settings, 'TASKER_SIGNUP_OPEN', True)).lower() not in ('0', 'false', 'no', '')


def google_client_id() -> str:
    return getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', '') or ''


class _PerAddressThrottle(SimpleRateThrottle):
    """Keyed on the client address, whoever (if anyone) is signed in."""

    def get_rate(self):
        # No rate configured (the test settings empty the table) = no limit,
        # rather than DRF's default of a 500 for a missing scope.
        return self.THROTTLE_RATES.get(self.scope)

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


class SignupBurstThrottle(_PerAddressThrottle):
    scope = 'signup'


class SignupDailyThrottle(_PerAddressThrottle):
    scope = 'signup_day'


class GoogleThrottle(_PerAddressThrottle):
    scope = 'google_auth'


class _PerAccountThrottle(_PerAddressThrottle):
    """Keyed on the signed-in account, so changing address does not help."""

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': request.user.pk}


class VerifyThrottle(_PerAccountThrottle):
    scope = 'verify_code'


class ResendThrottle(_PerAccountThrottle):
    scope = 'resend_code'


# --------------------------------------------------------------------- #
# Email confirmation codes
# --------------------------------------------------------------------- #

CODE_MINUTES = 15
CODE_MAX_ATTEMPTS = 5


def _hash(user, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f'{user.pk}:{code}'.encode(),
                    hashlib.sha256).hexdigest()


def send_code(user) -> bool:
    """
    Email a fresh 6-digit code, replacing any earlier one. True if sent.

    Never raises: a mail-server hiccup must not lose the account that was
    just created -- the person can press "send a new code".
    """
    from .tasker_brand import PRODUCT_NAME

    code = f'{secrets.randbelow(1_000_000):06d}'
    EmailCode.objects.update_or_create(user=user, defaults={
        'code_hash': _hash(user, code), 'attempts': 0,
        'expires_at': timezone.now() + timezone.timedelta(minutes=CODE_MINUTES),
    })
    try:
        send_mail(
            f'{code} is your {PRODUCT_NAME} code',
            f'Your {PRODUCT_NAME} confirmation code is {code}.\n\n'
            f'It expires in {CODE_MINUTES} minutes. If you did not sign up, '
            f'ignore this email -- nothing happens without the code.',
            settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False,
        )
        return True
    except Exception:
        logger.exception('[SIGNUP] could not send a code to user %s', user.pk)
        return False


def signed_in_response(request, user, status_code=status.HTTP_200_OK):
    """The same answer LoginView gives: a session (for audio) and a token."""
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    token, _ = Token.objects.get_or_create(user=user)
    activity, _ = TokenActivity.objects.get_or_create(token=token)
    activity.save()
    account = Account.objects.filter(user=user).first()
    identity = public_identity(user.username, account.role if account else None,
                               bool(account and account.email_verified_at))
    return Response({'token': token.key, 'user': {'username': user.username},
                     'capabilities': identity['capabilities'],
                     'email_verified': identity['email_verified']}, status=status_code)


#: Tasker passwords: shorter than staff's 12 (AUTH_PASSWORD_VALIDATORS), but
#: every character class is required, and Django's common-password and
#: looks-like-your-username checks still apply on top.
PASSWORD_MIN = 8
_PASSWORD_RULES = (
    (re.compile(r'[A-Z]'), 'an uppercase letter'),
    (re.compile(r'[a-z]'), 'a lowercase letter'),
    (re.compile(r'[0-9]'), 'a number'),
    (re.compile(r'[^A-Za-z0-9]'), 'a symbol such as _ ! @ # or -'),
)


def password_problems(password: str, username: str, email: str) -> list:
    """Everything wrong with a tasker's new password, in plain words."""
    from django.contrib.auth.password_validation import (
        CommonPasswordValidator, UserAttributeSimilarityValidator,
    )

    problems = []
    if len(password) < PASSWORD_MIN:
        problems.append(f'Use at least {PASSWORD_MIN} characters.')
    missing = [label for pattern, label in _PASSWORD_RULES if not pattern.search(password)]
    if missing:
        problems.append('Add ' + ', '.join(missing) + '.')
    if not problems:
        for validator in (CommonPasswordValidator(), UserAttributeSimilarityValidator()):
            try:
                validator.validate(password, User(username=username, email=email))
            except ValidationError as exc:
                problems.extend(exc.messages)
    return problems


def _clean_username(raw) -> str:
    return str(raw or '').strip()


def _username_problem(username: str):
    if not USERNAME_RE.match(username):
        return ('3–30 characters: letters, numbers, dots, dashes or underscores, '
                'starting with a letter or number.')
    if username.lower() in RESERVED:
        return 'That username is not available.'
    if User.objects.filter(username__iexact=username).exists():
        return 'That username is taken.'
    return None


class AuthOptionsView(views.APIView):
    """GET /workforce/auth/options/ -- what the sign-in page should offer."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            'signup_open': signup_open(),
            # A client id is public by design (it is in every Google button);
            # the secret half never exists on this flow.
            'google_client_id': google_client_id() or None,
        })


class SignupView(views.APIView):
    """POST /workforce/auth/signup/ {username, email, password, website}"""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [SignupBurstThrottle, SignupDailyThrottle]

    def post(self, request):
        if not signup_open():
            return Response({'error': 'Sign-up is closed at the moment.'},
                            status=status.HTTP_403_FORBIDDEN)

        # HONEYPOT. The field is hidden from people; bots fill every field.
        # Answered like any other refusal so a bot learns nothing from it.
        if str(request.data.get('website') or '').strip():
            logger.warning('[SIGNUP] honeypot filled from %s', request.META.get('REMOTE_ADDR'))
            return Response({'error': 'Sign-up failed. Please try again.'},
                            status=status.HTTP_400_BAD_REQUEST)

        username = _clean_username(request.data.get('username'))
        email = str(request.data.get('email') or '').strip().lower()
        password = str(request.data.get('password') or '')

        errors = {}
        problem = _username_problem(username)
        if problem:
            errors['username'] = problem
        try:
            validate_email(email)
            if len(email) > 254:
                raise ValidationError('too long')
            if User.objects.filter(email__iexact=email).exists():
                errors['email'] = 'An account already uses this email. Sign in instead.'
        except ValidationError:
            errors['email'] = 'Enter a valid email address.'
        if len(password) > 128:
            errors['password'] = 'Use 128 characters or fewer.'
        else:
            problems = password_problems(password, username, email)
            if problems:
                errors['password'] = ' '.join(problems)
        if errors:
            return Response({'error': 'Please fix the highlighted fields.', 'fields': errors},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                user = User.objects.create_user(username=username, email=email, password=password)
                Account.objects.create(user=user, role=roles.EAGLE_TASKER)
        except IntegrityError:
            return Response({'error': 'That username is taken.',
                             'fields': {'username': 'That username is taken.'}},
                            status=status.HTTP_400_BAD_REQUEST)

        logger.info('[SIGNUP] new tasker %s', username)
        send_code(user)
        return signed_in_response(request, user, status.HTTP_201_CREATED)


def _username_from_email(email: str) -> str:
    base = re.sub(r'[^A-Za-z0-9._-]', '', email.split('@')[0])[:20].strip('._-') or 'tasker'
    if len(base) < 3 or base.lower() in RESERVED:
        base = f'tasker{base}'[:20]
    candidate = base
    while User.objects.filter(username__iexact=candidate).exists():
        candidate = f'{base}{secrets.randbelow(9000) + 1000}'
    return candidate


def verify_google_credential(credential: str) -> dict:
    """Google's verdict on an ID token, or ValueError. Separate for tests."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    claims = id_token.verify_oauth2_token(
        credential, google_requests.Request(), google_client_id())
    if claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com'):
        raise ValueError('wrong issuer')
    return claims


class GoogleSignInView(views.APIView):
    """
    POST /workforce/auth/google/ {credential} -- sign in, or sign up, with Google.

    LINKING RULE. A Google account is matched by Google's own id (`sub`).
    If none is linked yet and an account here already uses the same email,
    it is NOT linked automatically: password accounts here never prove they
    own their email, so auto-linking would hand someone's Google sign-in to
    whoever registered their address first. That person is told to sign in
    with their password instead. Staff and worker accounts are never
    reachable through this door at all.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [GoogleThrottle]

    def post(self, request):
        if not google_client_id():
            return Response({'error': 'Google sign-in is not set up.'},
                            status=status.HTTP_404_NOT_FOUND)
        credential = str(request.data.get('credential') or '')
        if not credential or len(credential) > 4096:
            return Response({'error': 'Google sign-in failed. Please try again.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            claims = verify_google_credential(credential)
        except Exception as exc:
            logger.warning('[GOOGLE] rejected credential: %s', exc)
            return Response({'error': 'Google sign-in failed. Please try again.'},
                            status=status.HTTP_401_UNAUTHORIZED)
        if not claims.get('email_verified'):
            return Response({'error': 'Your Google email is not verified.'},
                            status=status.HTTP_401_UNAUTHORIZED)

        subject = str(claims['sub'])
        email = str(claims.get('email') or '').lower()

        link = ExternalIdentity.objects.select_related('user').filter(
            provider=ExternalIdentity.GOOGLE, subject=subject).first()
        if link:
            user = link.user
            role = Account.objects.filter(user=user).values_list('role', flat=True).first()
            if not user.is_active or role != roles.EAGLE_TASKER or user.is_staff:
                return Response({'error': 'This account cannot sign in here.'},
                                status=status.HTTP_403_FORBIDDEN)
            return signed_in_response(request, user)

        if User.objects.filter(email__iexact=email).exists():
            return Response({'error': 'An account already uses this email. '
                                      'Sign in with your username and password.'},
                            status=status.HTTP_409_CONFLICT)
        if not signup_open():
            return Response({'error': 'Sign-up is closed at the moment.'},
                            status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            user = User.objects.create_user(username=_username_from_email(email), email=email)
            user.set_unusable_password()
            user.save(update_fields=['password'])
            # Google already verified this address (email_verified above).
            Account.objects.create(user=user, role=roles.EAGLE_TASKER,
                                   email_verified_at=timezone.now())
            ExternalIdentity.objects.create(user=user, provider=ExternalIdentity.GOOGLE,
                                            subject=subject, email=email)
        logger.info('[SIGNUP] new tasker %s via Google', user.username)
        return signed_in_response(request, user, status.HTTP_201_CREATED)


def _tasker_account(user):
    account = Account.objects.filter(user=user).first()
    return account if account and account.role == roles.EAGLE_TASKER else None


class VerifyEmailView(views.APIView):
    """POST /workforce/auth/verify-email/ {code} -- confirm the account's email."""

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAuthenticated]
    throttle_classes = [VerifyThrottle]

    def post(self, request):
        account = _tasker_account(request.user)
        if account is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if account.email_verified_at:
            return Response({'email_verified': True})

        code = re.sub(r'\D', '', str(request.data.get('code') or ''))[:6]
        pending = EmailCode.objects.filter(user=request.user).first()
        if pending is None or pending.expires_at < timezone.now():
            return Response({'error': 'That code has expired. Send a new one.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if pending.attempts >= CODE_MAX_ATTEMPTS:
            return Response({'error': 'Too many wrong codes. Send a new one.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(code) != 6 or not hmac.compare_digest(pending.code_hash, _hash(request.user, code)):
            pending.attempts += 1
            pending.save(update_fields=['attempts'])
            return Response({'error': 'That code is not right. Check the email and try again.'},
                            status=status.HTTP_400_BAD_REQUEST)

        account.email_verified_at = timezone.now()
        account.save(update_fields=['email_verified_at', 'updated_at'])
        pending.delete()
        logger.info('[SIGNUP] %s confirmed their email', request.user.username)
        return Response({'email_verified': True})


class ResendCodeView(views.APIView):
    """POST /workforce/auth/resend-code/ -- email a new code."""

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAuthenticated]
    throttle_classes = [ResendThrottle]

    def post(self, request):
        account = _tasker_account(request.user)
        if account is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if account.email_verified_at:
            return Response({'email_verified': True})
        if not send_code(request.user):
            return Response({'error': 'We could not send the email. Try again in a minute.'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({'sent': True})


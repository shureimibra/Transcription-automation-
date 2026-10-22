"""
The Eagle tasker perimeter: one gate in front of the whole API.

WHY A MIDDLEWARE AND NOT ANOTHER PERMISSION CLASS. Every view in this
project declares its own permission_classes, and several of the older
transcription views admit ANY signed-in account (IsAuthenticatedUser) --
creating a transcription job, discovering a series, listing projects,
reading the AI budget. That was fine while every account was ours. An
outside Eagle tasker is not, and relying on each of ~40 views to remember
that is how one gets missed. So the rule is inverted for them: deny
everything, then allow a short list.

DEFAULT DENY, AND INDISTINGUISHABLE FROM "DOES NOT EXIST". A tasker who
calls anything outside the list gets the same 404 a mistyped URL gets --
not a 403, which would confirm the endpoint is there and merely forbidden.

THE IDENTITY IS RESOLVED HERE, NOT TRUSTED FROM DRF. DRF authenticates
inside the view, after middleware has run, so this resolves the token
itself (or reads the session user). An invalid token is left for the view
to reject as usual; only a valid token belonging to a perimeter role is
fenced.
"""
import re

from django.http import JsonResponse

from . import roles

#: Roles kept inside the perimeter. A new outside role is added here.
PERIMETER_ROLES = frozenset({roles.EAGLE_TASKER})

#: The only API paths a perimeter account can reach.
ALLOWED = (
    re.compile(r'^/api/v1/workforce/auth/(desk-login|logout|options|signup|google|verify-email|resend-code)/$'),
    re.compile(r'^/api/v1/workforce/me/$'),
    re.compile(r'^/api/v1/workforce/tasker/lookup/$'),
    re.compile(r'^/api/v1/workforce/tasker/tasks/[0-9a-f-]{36}/'),
)

_TOKEN = re.compile(r'^\s*Token\s+([0-9a-fA-F]{20,64})\s*$')


def _user_for(request):
    # Looking at the session marks it "accessed", which makes Django add
    # `Vary: Cookie` to every response -- including audio, whose headers
    # are kept free of any cookie mention. Our look is a gate check, not
    # something the response content depends on, so the flag is put back.
    session = getattr(request, 'session', None)
    was_accessed = getattr(session, 'accessed', None)
    user = getattr(request, 'user', None)
    authenticated = user is not None and user.is_authenticated
    if session is not None and was_accessed is not None:
        session.accessed = was_accessed
    if authenticated:
        return user
    match = _TOKEN.match(request.META.get('HTTP_AUTHORIZATION', ''))
    if not match:
        return None
    from rest_framework.authtoken.models import Token
    token = Token.objects.select_related('user').filter(key=match.group(1)).first()
    return token.user if token else None


def public_identity(username, role, email_verified=True) -> dict:
    """
    What a perimeter account is told about itself: its name and what it can
    reach. Not its internal role name, not an admin flag, not a profile --
    each would hint at a world outside the fence.
    """
    return {'username': username, 'capabilities': sorted(roles.capabilities(role)),
            'email_verified': bool(email_verified)}


def in_perimeter(user) -> bool:
    if user is None or not user.is_authenticated or user.is_staff or user.is_superuser:
        return False
    from .models import Account
    role = Account.objects.filter(user=user).values_list('role', flat=True).first()
    return role in PERIMETER_ROLES


class EagleTaskerPerimeterMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith('/api/') and not any(p.match(path) for p in ALLOWED):
            if in_perimeter(_user_for(request)):
                return JsonResponse({'detail': 'Not found.'}, status=404)
        return self.get_response(request)

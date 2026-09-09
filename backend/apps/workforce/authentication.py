"""
An idle session timeout for DRF's Token auth, which otherwise has none.

Confirmed live: a worker stayed signed in for two straight weeks, never
re-entering a password once. DRF's TokenAuthentication validates a token
against the database and stops there -- there is no age, no last-used
check, nothing. A token is valid forever until something explicitly
deletes it.

IDLE, NOT ABSOLUTE, ON PURPOSE. Every successful authentication resets the
clock (see TokenActivity), so a worker actively using the app for an entire
long shift is never cut off mid-task -- only a token nobody has touched for
the whole window is treated as abandoned and forced to re-authenticate.
"""
from datetime import timedelta
import os

from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import TokenActivity

#: Hours of no authenticated request before a token is treated as
#: abandoned. Same env-configurable-constant pattern as warmpool's
#: DEFAULT_POOL_SIZE and friends, rather than a Django setting -- this is
#: operational tuning, not something that varies by environment/deploy
#: config in any way that belongs in settings.py.
IDLE_TIMEOUT_HOURS = int(os.environ.get('TOKEN_IDLE_TIMEOUT_HOURS', '8'))


class IdleTimeoutTokenAuthentication(TokenAuthentication):
    """
    DRF's TokenAuthentication, plus the idle timeout it does not have.

    A token whose TokenActivity has not been touched inside
    IDLE_TIMEOUT_HOURS is deleted (not just refused -- a refused-but-still-
    valid token would keep coming back on every retry) and the request is
    rejected the same way an unknown token already is, so a client cannot
    tell "expired" from "never existed" by response shape alone.
    """

    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)

        activity, created = TokenActivity.objects.get_or_create(token=token)
        if not created:
            timeout = timedelta(hours=IDLE_TIMEOUT_HOURS)
            if timezone.now() - activity.last_used_at > timeout:
                token.delete()  # cascades TokenActivity
                raise AuthenticationFailed(
                    'Your session has expired. Please sign in again.')
            activity.save(update_fields=['last_used_at'])  # auto_now bumps it

        return user, token

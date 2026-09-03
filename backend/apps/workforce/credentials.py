"""
The operator's external-platform session, held server-side and encrypted.

THE RULE THIS FILE EXISTS TO ENFORCE. Workers authenticate against THIS
platform. The Eagle session belongs to the platform, not to them, and it
never travels toward a browser -- not in a serializer, not in a template,
not in an error message. Everything that needs it goes through
`get_cookie_header`, which returns a string that only ever reaches an
outbound HTTP call.

WHY NOT `TranscriptionJob.site_auth_headers`. That field currently holds a
live `Cookie` header on 105 rows, in plaintext, duplicated per job. It was a
reasonable shortcut when one person pasted their own cookie into their own
job; it is the wrong shape for a shared operator session because there is no
single place to rotate, no encryption, and a hundred copies to leak. This
model replaces it: ONE row per product, encrypted, rotatable without a
redeploy.

ENCRYPTION IS REAL BUT NOT MAGIC. Fernet with a key from the environment
protects the value at rest -- a database dump, a backup, a replica -- which
is the realistic exposure. It does NOT protect against someone who already
has both the database and the application's environment, because the
application must be able to decrypt in order to use it. Anyone claiming
otherwise is describing a system that cannot make requests.
"""
import base64
import hashlib
import logging
import os

from django.conf import settings
from django.db import models
from django.utils import timezone

logger = logging.getLogger('workforce')

TRANSCRIPTION = 'transcription'
TRANSLATION = 'translation'
PRODUCT_CHOICES = [
    (TRANSCRIPTION, 'Eagle Transcribe'),
    (TRANSLATION, 'Eagle Translate'),
]


def _fernet():
    """
    The cipher, keyed from the environment.

    `EAGLE_CREDENTIAL_KEY` should be a real Fernet key. When it is absent we
    derive one from SECRET_KEY rather than refusing to start -- a developer
    running locally should not have to generate a key before the app boots,
    and the value being protected in that case is their own test cookie.
    In production, set it: rotating SECRET_KEY would otherwise silently
    orphan every stored credential.
    """
    from cryptography.fernet import Fernet

    raw = os.environ.get('EAGLE_CREDENTIAL_KEY', '').strip()
    if raw:
        return Fernet(raw.encode() if isinstance(raw, str) else raw)

    derived = base64.urlsafe_b64encode(
        hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()
    )
    return Fernet(derived)


class EagleCredential(models.Model):
    """
    One row per Eagle product. The whole `Cookie` header, encrypted.

    Stored as an opaque blob rather than parsed into fields because the set
    of cookies that matters is the platform's business, not ours: today it
    is a Laravel session plus `cf_clearance` plus a remember-me token, and
    the moment we start modelling those individually we own a schema that
    breaks when they add one.
    """

    ACTIVE = 'active'
    EXPIRED = 'expired'
    UNKNOWN = 'unknown'
    STATUS_CHOICES = [
        (ACTIVE, 'Active'),
        (EXPIRED, 'Expired'),
        (UNKNOWN, 'Not yet verified'),
    ]

    product = models.CharField(max_length=20, choices=PRODUCT_CHOICES, unique=True)

    # The encrypted `Cookie` request header. Never rendered, never
    # serialized, never logged -- see `__str__` and the serializer, both of
    # which deliberately expose only metadata.
    cookie_encrypted = models.BinaryField(blank=True, null=True)

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=UNKNOWN)

    # Set by the health check, so an operator can see at a glance whether
    # the session is still good without anyone pasting it anywhere.
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    last_failure_detail = models.TextField(blank=True, default='')

    # What the platform says about the account. Useful operationally --
    # minutes balance is the real throughput cap -- and safe to show.
    account_label = models.CharField(max_length=120, blank=True, default='')
    minutes_balance = models.IntegerField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='eagle_credentials',
    )

    class Meta:
        ordering = ['product']

    def __str__(self):
        # Deliberately no cookie material, because __str__ ends up in logs.
        return f'{self.get_product_display()} ({self.status})'

    # -- the only two doors in or out ---------------------------------- #

    def set_cookie(self, cookie_header: str, actor=None) -> None:
        value = (cookie_header or '').strip()
        if not value:
            raise ValueError('Refusing to store an empty cookie header.')
        self.cookie_encrypted = _fernet().encrypt(value.encode('utf-8'))
        self.status = self.UNKNOWN
        self.last_failure_detail = ''
        if actor is not None and getattr(actor, 'is_authenticated', False):
            self.updated_by = actor

    def rotate_cookie(self, cookie_header: str) -> bool:
        """
        Adopt a session cookie the platform reissued mid-conversation.

        DISTINCT FROM `set_cookie`, deliberately. `set_cookie` is an
        operator storing a NEW credential, so it resets the health record to
        UNKNOWN -- the stored session is unproven until something verifies
        it. A rotation is the opposite: the same session, still logged in,
        which the server has simply renamed. Resetting health on every call
        would flap the status all day and make the dashboard useless.

        WHY THIS IS NEEDED AT ALL. Eagle reissues a cookie on essentially
        every request. A client that replays the value it was given at setup
        is presenting a session the server has moved past; it works for a
        while and then stops, with nothing in between to explain it. Keeping
        the rotation is what makes a session last beyond one process.

        Returns True when something actually changed.
        """
        value = (cookie_header or '').strip()
        if not value or value == self.get_cookie_header():
            return False
        self.cookie_encrypted = _fernet().encrypt(value.encode('utf-8'))
        self.save(update_fields=['cookie_encrypted', 'updated_at'])
        return True

    def get_cookie_header(self) -> str:
        """
        The decrypted header. THE ONLY read path.

        Returns '' rather than raising when absent or undecryptable, so a
        missing credential surfaces as "not configured" at the call site
        instead of a 500 halfway through a poll.
        """
        if not self.cookie_encrypted:
            return ''
        try:
            raw = bytes(self.cookie_encrypted)
            return _fernet().decrypt(raw).decode('utf-8')
        except Exception as exc:
            # Almost always a changed key. Say so without echoing the blob.
            logger.error('[EAGLE] could not decrypt %s credential: %s',
                         self.product, type(exc).__name__)
            return ''

    @property
    def is_usable(self) -> bool:
        return bool(self.cookie_encrypted) and self.status != self.EXPIRED

    # -- health ---------------------------------------------------------- #

    def mark_verified(self, account_label: str = '', minutes_balance=None) -> None:
        self.status = self.ACTIVE
        self.last_verified_at = timezone.now()
        self.last_failure_detail = ''
        if account_label:
            self.account_label = account_label[:120]
        if minutes_balance is not None:
            self.minutes_balance = minutes_balance
        self.save(update_fields=[
            'status', 'last_verified_at', 'last_failure_detail',
            'account_label', 'minutes_balance', 'updated_at',
        ])

    def mark_expired(self, detail: str = '') -> None:
        self.status = self.EXPIRED
        self.last_failure_at = timezone.now()
        self.last_failure_detail = (detail or '')[:2000]
        self.save(update_fields=[
            'status', 'last_failure_at', 'last_failure_detail', 'updated_at',
        ])


def get_credential(product: str):
    """The stored credential for a product, or None."""
    return EagleCredential.objects.filter(product=product).first()


def cookie_header_for(product: str) -> str:
    """
    Convenience for the clients. Empty string when unconfigured, which the
    caller turns into `EagleNotConfigured`.
    """
    credential = get_credential(product)
    return credential.get_cookie_header() if credential else ''

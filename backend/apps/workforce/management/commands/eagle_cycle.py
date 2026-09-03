"""
One turn of the Eagle loop: sync, warm, purge.

RUN THIS ON A SCHEDULE. Every 30-60 seconds is the intended cadence, and the
poll doubles as the session keep-alive -- Laravel idles a session out after
about two hours, so a system that talks to Eagle every minute never goes
stale. That is why "keeping the cookie alive" needs no separate mechanism.

IT NEVER CLAIMS AND NEVER SUBMITS. Discovery and pre-processing only. A claim
starts a one-hour deadline and spends purchased minutes, so it happens when a
worker asks for work -- never on a timer, where nobody is waiting to do it.

    manage.py eagle_cycle                 # one turn, then exit
    manage.py eagle_cycle --loop          # keep going, for a dev machine
    manage.py eagle_cycle --no-warm       # discovery only
    manage.py eagle_cycle --dry-run       # report, change nothing
"""
import logging
import time

from django.core.management.base import BaseCommand

from apps.workforce import warmpool
from apps.workforce.credentials import EagleCredential
from apps.workforce.sync import sync_all



def public_ip() -> str:
    """
    The address Cloudflare sees us coming from.

    WHY A POLLER LOGS ITS OWN IP. `cf_clearance` -- the token that gets us
    past Cloudflare -- is bound to the IP that earned it. On a fixed
    connection that is invisible; on a mobile hotspot it is the whole story,
    because carrier NAT reassigns the public address every few hours and
    every reassignment silently invalidates the token.

    Recording it each cycle turns "the session died again" into something
    checkable: if the address changed at the same time, the network is the
    cause and no amount of cookie handling on our side will fix it.

    Best effort. A diagnostic that can fail a poll is worse than no
    diagnostic, and this one talks to a service that returns nothing but the
    caller's own address.
    """
    import urllib.request

    try:
        request = urllib.request.Request(
            'https://www.cloudflare.com/cdn-cgi/trace',
            headers={'User-Agent': 'eagle-cycle/1.0'},
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            for line in response.read().decode('utf-8', 'replace').splitlines():
                if line.startswith('ip='):
                    return line[3:].strip()
    except Exception:
        pass
    return ''



logger = logging.getLogger('eagle')


class Command(BaseCommand):
    help = 'Sync Eagle tasks, top up the warm pool, and purge expired audio.'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true',
                            help='Repeat until interrupted (development).')
        parser.add_argument('--interval', type=int, default=45,
                            help='Seconds between turns when looping.')
        parser.add_argument('--no-warm', action='store_true',
                            help='Discover tasks but do not pre-process them.')
        parser.add_argument('--no-purge', action='store_true',
                            help='Skip the retention sweep.')
        parser.add_argument('--pool-size', type=int, default=None)
        parser.add_argument('--concurrency', type=int, default=None)
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what is configured and stop.')

    def handle(self, *args, **options):
        if options['dry_run']:
            self._report_configuration()
            return

        if not options['loop']:
            self._turn(options)
            return

        interval = max(10, options['interval'])
        self.stdout.write(f'Looping every {interval}s. Ctrl+C to stop.')
        try:
            while True:
                self._turn(options)
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\nStopped.'))

    # ------------------------------------------------------------------ #

    _last_ip = None

    def _report_ip(self):
        """
        THROUGH THE LOGGER, NOT stdout.

        `self.stdout.write` is block-buffered the moment the command's
        output is redirected to a file -- which is how it runs as a service.
        The first version of this printed nothing at all for hours: the
        lines existed, sat in a buffer, and the one diagnostic written to
        explain a mystery became part of it. Logging goes out unbuffered and
        interleaves with everything else in the same file.
        """
        address = public_ip()
        if not address:
            return
        if self._last_ip and address != self._last_ip:
            logger.warning(
                '[NETWORK] public IP changed %s -> %s. Cloudflare clearance '
                'is bound to the address that earned it, so any Eagle '
                'session stored before now is probably dead through no '
                'fault of the cookie handling.', self._last_ip, address)
        elif self._last_ip is None:
            logger.info('[NETWORK] calling from %s', address)
        self._last_ip = address

    def _turn(self, options):
        self._report_ip()
        for report in sync_all():
            style = self.style.SUCCESS if report.session_ok else self.style.ERROR
            self.stdout.write(style(
                f'[sync:{report.product}] seen={report.seen} new={report.created} '
                f'updated={report.updated} taken={report.taken_by_others} '
                f'minutes={report.minutes_balance}'
            ))
            for error in report.errors[:5]:
                self.stdout.write(self.style.WARNING(f'    {error}'))

        if not options['no_warm']:
            warm = warmpool.run_cycle(
                pool_size=options['pool_size'], concurrency=options['concurrency'],
            )
            self.stdout.write(
                f'[warm] ready={warm.already_warm} processed={warm.processed} '
                f'asr_only={warm.stage_a_only} deferred={warm.deferred} '
                f'failed={warm.failed}'
            )
            for error in warm.errors[:5]:
                self.stdout.write(self.style.WARNING(f'    {error}'))

        if not options['no_purge']:
            purge = warmpool.purge_expired_audio()
            if purge['files_removed']:
                freed = purge['bytes_freed'] / (1024 * 1024)
                self.stdout.write(
                    f"[purge] removed {purge['files_removed']} files ({freed:.1f} MB)")

    def _report_configuration(self):
        self.stdout.write(self.style.MIGRATE_HEADING('Eagle cycle configuration'))
        self.stdout.write(f'  warm pool target : {warmpool.DEFAULT_POOL_SIZE}')
        self.stdout.write(f'  concurrency      : {warmpool.DEFAULT_CONCURRENCY}'
                          '   (each pipeline holds ~750 MB of models)')
        self.stdout.write(f'  audio retention  : {warmpool.AUDIO_RETENTION_DAYS} days')
        self.stdout.write(f'  lost-race audio  : {warmpool.LOST_RACE_RETENTION_HOURS} h')
        self.stdout.write(f'  media cache      : {warmpool.cache_dir()}')
        self.stdout.write('')

        credentials = EagleCredential.objects.all()
        if not credentials:
            self.stdout.write(self.style.WARNING(
                '  No Eagle credentials stored. Add them with '
                '`manage.py eagle_credential`; nothing will sync until then.'))
            return

        for credential in credentials:
            style = (self.style.SUCCESS if credential.status == EagleCredential.ACTIVE
                     else self.style.WARNING)
            self.stdout.write(style(
                f'  {credential.product:14} {credential.status:8} '
                f'minutes={credential.minutes_balance} '
                f'verified={credential.last_verified_at:%Y-%m-%d %H:%M}'
                if credential.last_verified_at else
                f'  {credential.product:14} {credential.status:8} never verified'
            ))

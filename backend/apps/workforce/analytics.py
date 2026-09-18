"""
Per-worker throughput and timing over a date window.

WHY A SEPARATE ENDPOINT FROM /stats/. `WorkforceStatsView` answers "what is
happening right now" and is deliberately unfiltered; this answers "how did
the team do between these dates". Merging them would mean either
date-filtering the live counters -- a queue depth has no date range -- or
serving two unrelated shapes from one handler.

RATES ARE NULL, NOT ZERO, WHEN THERE IS NOTHING TO DIVIDE. A worker who has
submitted nothing this week has no approval rate. Reporting 0% would read as
"everything they sent was rejected", which is a different and damaging claim
about a real person's work. The frontend renders null as an em dash.

COUNTED BY OUTCOME TIMESTAMP, NOT BY CURRENT STATUS. A task approved on
Monday and later superseded still counts as Monday's approval. Filtering on
`status` instead would silently rewrite last month's figures every time a
task moved today.

NOTHING HERE IS STORED. Every number is derived from the timestamps already
on Task, so there is no rollup table that can drift away from the source.
"""
import datetime as dt

from django.utils import timezone as dj_timezone
from django.utils.dateparse import parse_date
from rest_framework import status, views
from rest_framework.response import Response

from . import states
from .models import Task, WorkerProfile
from .permissions import IsAdmin
from .views import AUTH_CLASSES

#: Widest window computed in one request. A year of tasks for six workers is
#: small, but the bound keeps an unbounded scan from being one hand-typed
#: query parameter away.
MAX_WINDOW_DAYS = 400

DEFAULT_WINDOW_DAYS = 29

#: The business's own stated targets -- 90 tasks submitted a day, 92%+ of
#: decided outcomes accepted. Both are business facts, not measurements, so
#: they live here as constants rather than being derived from the data --
#: the frontend compares the real numbers against these rather than each
#: computing (or hardcoding, twice, and drifting) its own copy.
DAILY_SUBMISSION_TARGET = 90
PASS_RATE_TARGET = 0.92


def _parse_day(raw):
    """A YYYY-MM-DD query parameter, or None. Raises ValueError on garbage."""
    if not raw:
        return None
    parsed = parse_date(str(raw)[:10])
    if parsed is None:
        raise ValueError(raw)
    return parsed


def _start_of_day(day):
    """Midnight local time, so "the 5th" means the operator's 5th, not UTC's."""
    naive = dt.datetime.combine(day, dt.time.min)
    if dj_timezone.is_naive(naive):
        return dj_timezone.make_aware(naive, dj_timezone.get_current_timezone())
    return naive


def _span(start, end):
    """Seconds between two timestamps, or None if either is missing."""
    if not start or not end:
        return None
    seconds = (end - start).total_seconds()
    # A negative span means the clock or the data moved backwards. Drop it
    # rather than letting it drag a figure into nonsense.
    return seconds if seconds >= 0 else None


def _ratio(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator, 3)


def _median(values, digits=1):
    """
    Median rather than mean: one task left open over a weekend would
    otherwise dominate a week of ordinary work.

    `digits` is not decoration. One decimal is plenty for a duration in
    seconds, but the realtime factor lives between 0 and about 2, where
    rounding to one decimal turns 0.75x -- exactly the 15-minutes-per-
    20-minutes-of-audio target -- into 0.8x, and a team hitting the target
    reads as a team missing it.
    """
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return None
    middle = len(clean) // 2
    if len(clean) % 2:
        return round(clean[middle], digits)
    return round((clean[middle - 1] + clean[middle]) / 2, digits)


class WorkforcePerformanceView(views.APIView):
    """GET /api/v1/workforce/stats/performance/?from=YYYY-MM-DD&to=YYYY-MM-DD"""

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAdmin]

    def get(self, request):
        today = dj_timezone.localdate()
        try:
            end = _parse_day(request.query_params.get('to')) or today
            start = (
                _parse_day(request.query_params.get('from'))
                or (end - dt.timedelta(days=DEFAULT_WINDOW_DAYS))
            )
        except ValueError:
            return Response({'error': 'Dates must be in YYYY-MM-DD format.'},
                            status=status.HTTP_400_BAD_REQUEST)

        if start > end:
            return Response({'error': 'The start date must be on or before the end date.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if (end - start).days > MAX_WINDOW_DAYS:
            return Response(
                {'error': f'Please choose a range of {MAX_WINDOW_DAYS} days or fewer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Inclusive of both endpoints.
        window_start = _start_of_day(start)
        window_end = _start_of_day(end + dt.timedelta(days=1))

        rows = [
            self._worker_row(profile, window_start, window_end)
            for profile in WorkerProfile.objects.select_related('user').order_by('worker_code')
        ]

        submitted = sum(row['submitted'] for row in rows)
        approved = sum(row['approved'] for row in rows)
        rejected = sum(row['rejected'] for row in rows)
        decided = approved + rejected
        window_days = (end - start).days + 1

        # PATTERN-SPOTTING, NOT JUST A COUNT. A rejection is a permanent,
        # unappealable loss (states.py's TERMINAL_STATES), so seeing the
        # real reasons together -- across every worker, in one place -- is
        # what tells an operator whether the same defect keeps recurring
        # (a prompt problem) or outcomes are scattered (something else).
        recent_rejections = [
            {
                'id': str(task.id),
                'external_task_id': task.external_task_id,
                'kind': task.kind,
                'rejected_at': task.rejected_at,
                'rejection_reason': task.rejection_reason,
                'worker_code': task.claimed_by.worker_code if task.claimed_by_id else None,
            }
            for task in Task.objects.filter(
                status=states.REJECTED,
                rejected_at__gte=window_start, rejected_at__lt=window_end,
            ).select_related('claimed_by').order_by('-rejected_at')[:30]
        ]

        return Response({
            'range': {'from': start.isoformat(), 'to': end.isoformat()},
            'totals': {
                'submitted': submitted,
                'approved': approved,
                'rejected': rejected,
                'decided': decided,
                'approval_rate': round(approved / decided, 4) if decided else None,
                'rejection_rate': round(rejected / decided, 4) if decided else None,
                'created': Task.objects.filter(
                    created_at__gte=window_start, created_at__lt=window_end).count(),
                'avg_submitted_per_day': round(submitted / window_days, 1),
                'daily_target': DAILY_SUBMISSION_TARGET,
                'pass_rate_target': PASS_RATE_TARGET,
            },
            'workers': rows,
            'recent_rejections': recent_rejections,
        })

    @staticmethod
    def _worker_row(profile, window_start, window_end):
        owned = Task.objects.filter(claimed_by=profile)
        submitted = list(
            owned.filter(submitted_at__gte=window_start, submitted_at__lt=window_end)
            .values('claimed_at', 'started_at', 'ready_at', 'submitted_at', 'duration_seconds')
        )
        approved = owned.filter(
            approved_at__gte=window_start, approved_at__lt=window_end).count()
        rejected = owned.filter(
            rejected_at__gte=window_start, rejected_at__lt=window_end).count()
        decided = approved + rejected

        handling = [_span(t['claimed_at'], t['submitted_at']) for t in submitted]

        return {
            'worker_code': profile.worker_code,
            'display_name': profile.display_name or profile.user.username,
            'status': profile.status,
            'submitted': len(submitted),
            'approved': approved,
            'rejected': rejected,
            'decided': decided,
            'approval_rate': round(approved / decided, 4) if decided else None,
            'rejection_rate': round(rejected / decided, 4) if decided else None,
            'held_now': owned.filter(status__in=states.WORKER_HELD_STATES).count(),
            # Claim to submission: the figure the 15-minutes-per-20-minute
            # target is measured against.
            'median_handling_seconds': _median(handling),
            'median_editing_seconds': _median(
                [_span(t['started_at'], t['ready_at']) for t in submitted]
            ),
            'median_realtime_factor': _median([
                _ratio(_span(t['claimed_at'], t['submitted_at']), t['duration_seconds'])
                for t in submitted
            ], digits=2),
            'audio_seconds': sum(t['duration_seconds'] or 0 for t in submitted),
        }

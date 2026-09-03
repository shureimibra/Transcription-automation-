"""
Tests for the date-scoped performance report.

THE CLAIM WORTH TESTING is not that the arithmetic works -- it is that the
report refuses to make a statement the data does not support. A worker with
nothing decided has no approval rate, and saying 0% would be a specific,
damaging and false claim about their work. That is asserted here directly,
because it is the kind of thing a later "simplification" quietly reverts.

Everything is written with explicit timestamps rather than by driving the
service layer, so a test about counting windows does not also depend on the
state machine's rules for reaching those states.
"""
import datetime as dt

from django.utils import timezone
from rest_framework.test import APITestCase

from .models import Task
from .tests import make_task, make_worker


def days_ago(n):
    return timezone.now() - dt.timedelta(days=n)


class PerformanceReportTestCase(APITestCase):

    def setUp(self):
        self.admin_user, self.admin = make_worker('boss', 'A-01', is_admin=True)
        self.worker_user, self.worker = make_worker('ana', 'W-01')
        self.client.force_authenticate(self.admin_user)
        self.url = '/api/v1/workforce/stats/performance/'

    def _submitted(self, days, claimed_offset_seconds=600, duration=1200, **extra):
        """A task this worker submitted `days` ago."""
        submitted_at = days_ago(days)
        return make_task(
            claimed_by=self.worker,
            status=extra.pop('status', 'submitted'),
            duration_seconds=duration,
            claimed_at=submitted_at - dt.timedelta(seconds=claimed_offset_seconds),
            started_at=submitted_at - dt.timedelta(seconds=claimed_offset_seconds - 60),
            ready_at=submitted_at - dt.timedelta(seconds=30),
            submitted_at=submitted_at,
            **extra,
        )

    # -- access -------------------------------------------------------- #

    def test_a_worker_cannot_read_the_report(self):
        self.client.force_authenticate(self.worker_user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_an_anonymous_caller_cannot_read_the_report(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    # -- the null-not-zero rule ---------------------------------------- #

    def test_approval_rate_is_null_when_nothing_has_been_decided(self):
        self._submitted(days=1)

        row = self._row_for('W-01')

        self.assertEqual(row['submitted'], 1)
        self.assertEqual(row['decided'], 0)
        # The whole point: not 0.0.
        self.assertIsNone(row['approval_rate'])
        self.assertIsNone(row['rejection_rate'])

    def test_timings_are_null_for_a_worker_with_no_submissions(self):
        row = self._row_for('W-01')

        self.assertEqual(row['submitted'], 0)
        self.assertIsNone(row['median_handling_seconds'])
        self.assertIsNone(row['median_realtime_factor'])

    def test_approval_rate_is_computed_once_there_are_decisions(self):
        self._submitted(days=2, approved_at=days_ago(1), status='approved')
        self._submitted(days=2, approved_at=days_ago(1), status='approved')
        self._submitted(days=2, rejected_at=days_ago(1), status='rejected')

        row = self._row_for('W-01')

        self.assertEqual(row['decided'], 3)
        self.assertAlmostEqual(row['approval_rate'], 2 / 3, places=3)
        self.assertAlmostEqual(row['rejection_rate'], 1 / 3, places=3)

    # -- windowing ----------------------------------------------------- #

    def test_work_outside_the_window_is_excluded(self):
        self._submitted(days=200)

        row = self._row_for('W-01')  # default window is the last 30 days

        self.assertEqual(row['submitted'], 0)

    def test_an_explicit_range_includes_both_endpoints(self):
        self._submitted(days=5)
        day = (timezone.localtime(days_ago(5))).date().isoformat()

        response = self.client.get(self.url, {'from': day, 'to': day})

        self.assertEqual(response.status_code, 200)
        row = next(r for r in response.data['workers'] if r['worker_code'] == 'W-01')
        self.assertEqual(row['submitted'], 1)

    def test_outcomes_are_counted_by_their_own_timestamp_not_current_status(self):
        # Approved inside the window, but since moved on to something else.
        # The approval still belongs to the day it happened.
        self._submitted(days=3, approved_at=days_ago(2), status='failed')

        row = self._row_for('W-01')

        self.assertEqual(row['approved'], 1)

    # -- input handling ------------------------------------------------ #

    def test_a_malformed_date_is_a_400_with_a_readable_message(self):
        response = self.client.get(self.url, {'from': 'last-tuesday'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('YYYY-MM-DD', response.data['error'])

    def test_a_backwards_range_is_rejected(self):
        response = self.client.get(self.url, {'from': '2026-05-01', 'to': '2026-04-01'})

        self.assertEqual(response.status_code, 400)
        self.assertIn('on or before', response.data['error'])

    def test_an_absurd_range_is_refused_rather_than_scanned(self):
        response = self.client.get(self.url, {'from': '1999-01-01', 'to': '2026-01-01'})

        self.assertEqual(response.status_code, 400)

    # -- timing -------------------------------------------------------- #

    def test_handling_time_is_measured_from_claim_to_submission(self):
        self._submitted(days=1, claimed_offset_seconds=900, duration=1200)

        row = self._row_for('W-01')

        self.assertAlmostEqual(row['median_handling_seconds'], 900, delta=2)
        # 900s of work on 1200s of audio.
        self.assertAlmostEqual(row['median_realtime_factor'], 0.75, delta=0.01)

    def test_the_median_ignores_a_single_outlier(self):
        self._submitted(days=1, claimed_offset_seconds=600)
        self._submitted(days=1, claimed_offset_seconds=660)
        self._submitted(days=1, claimed_offset_seconds=100000)

        row = self._row_for('W-01')

        # A mean would be over 33000. The median is the middle task.
        self.assertAlmostEqual(row['median_handling_seconds'], 660, delta=5)

    def test_a_missing_timestamp_does_not_produce_a_zero(self):
        # Submitted, but claimed_at was never recorded. The task still counts
        # as submitted; it simply contributes no timing.
        make_task(
            claimed_by=self.worker, status='submitted', duration_seconds=1200,
            claimed_at=None, submitted_at=days_ago(1),
        )

        row = self._row_for('W-01')

        self.assertEqual(row['submitted'], 1)
        self.assertIsNone(row['median_handling_seconds'])

    # -- totals -------------------------------------------------------- #

    def test_totals_aggregate_across_workers(self):
        _, other = make_worker('bo', 'W-02')
        self._submitted(days=1, approved_at=days_ago(1), status='approved')
        make_task(claimed_by=other, status='approved', submitted_at=days_ago(1),
                  approved_at=days_ago(1), duration_seconds=600)

        response = self.client.get(self.url)

        self.assertEqual(response.data['totals']['submitted'], 2)
        self.assertEqual(response.data['totals']['approved'], 2)
        self.assertEqual(response.data['totals']['approval_rate'], 1.0)

    def test_totals_approval_rate_is_null_with_no_decisions(self):
        response = self.client.get(self.url)

        self.assertIsNone(response.data['totals']['approval_rate'])

    # -- helper -------------------------------------------------------- #

    def _row_for(self, worker_code, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return next(r for r in response.data['workers'] if r['worker_code'] == worker_code)

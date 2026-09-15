"""
The workforce API.

EVERY ENDPOINT HERE REQUIRES AUTHENTICATION. That is a deliberate departure
from the legacy transcription API, which Phase 1 recorded as running under
`AllowAny` with no authentication classes at all. Those endpoints are not
changed in this phase -- doing so would touch 94 call sites across 23 test
classes and belongs with the rest of the security hardening -- but nothing
new is built on that footing.

NO ENDPOINT READS A WORKER ID FROM THE REQUEST. The acting worker is always
`profile_of(request.user)`. A body containing `worker_id` is ignored, which
is what makes the ownership rules in permissions.py meaningful rather than
advisory.
"""
import logging

from django.contrib.auth import authenticate, login, logout
from django.db.models import Count, Q
from rest_framework import status, views, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.authentication import SessionAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from . import roles, services, states
from .authentication import IdleTimeoutTokenAuthentication
from .models import Account, Task, TaskEvent, TokenActivity, WorkerProfile, ready_for_work_q
from .permissions import (
    IsAdmin, IsAuthenticatedWorkerOrAdmin, TaskAccessPermission, account_of,
    account_role, is_admin, visible_tasks_for, worker_profile,
)
from .serializers import (
    AccountCreateSerializer, AccountSerializer, AdminTaskSerializer,
    AdminWorkerSerializer, TaskCreateSerializer, TaskEventSerializer,
    TaskSerializer, WorkerCreateSerializer, WorkerProfileSerializer,
)

logger = logging.getLogger('workforce')

# Token for the browser and for scripts, Session as the fallback. Both are
# declared explicitly on every view rather than relying on a project default,
# because the project default is currently an empty list.
#
# THE ORDER IS LOAD-BEARING. DRF uses the FIRST authenticator that returns a
# user, and SessionAuthentication enforces CSRF when it does. Because
# LoginView also opens a Django session -- the WebSocket consumer needs it,
# since a browser cannot set an Authorization header on a socket -- every
# subsequent browser request carries both a session cookie and a token. With
# Session first, DRF would authenticate by cookie and then reject the request
# for a missing CSRF token, and every worker action would fail with a 403.
# Token first means the header wins and the cookie is never consulted.
#
# Session stays in the list so a browser session alone still works (the
# frontend does send X-CSRFToken when Django has set the cookie).
AUTH_CLASSES = [IdleTimeoutTokenAuthentication, SessionAuthentication]


class LoginView(views.APIView):
    """
    Username/password exchange for a session cookie and an API token.

    Deliberately vague on failure: "invalid credentials" for a wrong
    password, an unknown username and a disabled account alike. Telling an
    attacker which usernames exist is a free gift, and with six workers the
    account list is small enough to enumerate quickly.

    NO AUTHENTICATORS AT ALL, DELIBERATELY. This endpoint's whole job is to
    ESTABLISH an identity from a username and password; it has no business
    reading one the caller already has. With SessionAuthentication in the
    list, a stale session cookie made DRF authenticate the request before
    reading the credentials -- and then enforce CSRF on it, so signing in
    again after an earlier session failed with

        CSRF Failed: Origin checking failed - http://localhost:3000 does
        not match any trusted origins.

    which is both baffling to the person and unrelated to their password.
    `login()` below still opens a session; nothing is read from one.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    # THE ONE ENDPOINT ANYBODY CAN REACH, so it is the one that gets
    # guessed at. Ten attempts a minute is generous for somebody mistyping
    # a password and useless for working through a wordlist.
    throttle_scope = 'login'

    def post(self, request):
        username = str(request.data.get('username') or '').strip()
        password = str(request.data.get('password') or '')
        if not username or not password:
            return Response(
                {'error': 'Username and password are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request, username=username, password=password)
        if user is None or not user.is_active:
            logger.warning('[WORKFORCE] failed login for %r', username[:64])
            return Response(
                {'error': 'Invalid credentials.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # SIGNING IN AND TAKING TASKS ARE DIFFERENT PERMISSIONS.
        #
        # This used to be one check: no active WorkerProfile meant no login
        # at all, which made a tool-only account -- someone allowed to use
        # the transcription studio but not part of the workforce --
        # impossible to represent. Now the role decides what happens next,
        # and only a TASKER is required to have a working profile.
        role = account_role(user)
        profile = WorkerProfile.objects.filter(user=user).first()

        if role == roles.TASKER and (profile is None or not profile.is_active_worker):
            # Authenticated, is a tasker, but suspended or without a
            # profile. Distinct from bad credentials, because the person
            # can act on it -- they need an administrator, not another
            # password attempt.
            logger.warning('[WORKFORCE] inactive worker login blocked: %s', user.username)
            return Response(
                {'error': 'This account is not active for task work.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        login(request, user)
        token, _ = Token.objects.get_or_create(user=user)
        # A REUSED TOKEN'S CLOCK RESETS ON SIGN-IN TOO, not just on API use.
        # get_or_create above hands back the SAME token if the previous one
        # had not yet gone idle -- signing in is itself activity, so its
        # TokenActivity is touched here rather than waiting for the first
        # authenticated request after login to do it implicitly.
        activity, _ = TokenActivity.objects.get_or_create(token=token)
        activity.save()
        if profile:
            profile.touch()

        return Response({
            'token': token.key,
            'user': {
                'username': user.username,
                'role': role,
                # Kept for compatibility with anything already reading it.
                # `role` is the field to use.
                'is_admin': is_admin(user),
            },
            'capabilities': sorted(roles.capabilities(role)),
            'profile': WorkerProfileSerializer(profile).data if profile else None,
        })


class LogoutView(views.APIView):
    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # The token is rotated, not merely forgotten client-side: a logout
        # that leaves a working token behind has not logged anything out.
        Token.objects.filter(user=request.user).delete()
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentUserView(views.APIView):
    """Who am I, and what may I do. The frontend's bootstrap call."""

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = worker_profile(request.user)
        role = account_role(request.user)
        return Response({
            'username': request.user.username,
            'role': role,
            # What this account may reach, sent explicitly so the frontend
            # renders navigation from the server's answer instead of
            # re-deriving it from a role string it might interpret
            # differently. Adding a role does not require a frontend change.
            'capabilities': sorted(roles.capabilities(role)),
            'is_admin': is_admin(request.user),
            'profile': WorkerProfileSerializer(profile).data if profile else None,
        })


class TaskPagination(PageNumberPagination):
    """
    Page the task list the way Eagle's own tables do.

    WHY IT IS OPT-IN. `?page=` turns it on; without it the response is a
    plain list exactly as before. Every existing caller -- the dashboard,
    the realtime refresh, the tests -- keeps working unchanged, and only the
    browsable queue asks for pages. Silently wrapping every response in
    {count, results} would break each of them at once.

    The count in the envelope is the count for the FILTER, so "74 available"
    stays true regardless of which page you are looking at.
    """
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100

    def paginate_queryset(self, queryset, request, view=None):
        if 'page' not in request.query_params:
            return None
        return super().paginate_queryset(queryset, request, view=view)

    def get_paginated_response(self, data):
        # READY COUNT, OVER THE FULL FILTER, NOT THE PAGE. A queue view
        # counting "ready now" over only its own loaded page caps the number
        # at page_size regardless of how many are actually ready -- a real
        # bug (25 shown when 40+ were ready). self.page.paginator.object_list
        # is the queryset AFTER every filter but BEFORE the page slice, so
        # this one extra count query answers the question the page slice
        # cannot.
        ready_count = self.page.paginator.object_list.filter(ready_for_work_q()).count()
        response = super().get_paginated_response(data)
        response.data['ready_count'] = ready_count
        return response


class TaskViewSet(viewsets.ModelViewSet):
    """
    Tasks, scoped to what the caller may see.

    Creation and deletion are admin-only; everything else is available to a
    worker for the tasks they can reach. State changes are actions rather
    than PATCHes, so each one lands in the service layer under a row lock.
    """

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAuthenticatedWorkerOrAdmin, TaskAccessPermission]

    pagination_class = TaskPagination

    def get_queryset(self):
        queryset = visible_tasks_for(self.request.user).select_related(
            'claimed_by', 'assigned_to', 'job',
        )
        if self.request.query_params.get('ready') == 'true':
            queryset = queryset.filter(ready_for_work_q())

        # The two kinds are different jobs with different instructions and
        # wildly different supply (a handful of transcription tasks against
        # seventy translations). Mixing them in one list makes the smaller
        # kind effectively invisible.
        kind = self.request.query_params.get('kind')
        if kind in dict(Task.KIND_CHOICES):
            queryset = queryset.filter(kind=kind)
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status__in=status_filter.split(','))
        if self.request.query_params.get('mine') == 'true':
            profile = worker_profile(self.request.user)
            queryset = queryset.filter(claimed_by=profile) if profile else queryset.none()
        if self.request.query_params.get('available') == 'true':
            queryset = queryset.filter(
                status__in=states.CLAIMABLE_STATES, claimed_by__isnull=True,
            )

        # READY WORK FIRST. A tasker scanning the queue should meet the tasks
        # that can be started immediately; a cold task looks identical but
        # starts the external clock on work the machine has not begun.
        # Shortest first within each group, because a short task clears the
        # deadline more easily and turns an hour into more completed work.
        #
        # "Ready" here MUST agree with TaskSerializer.get_is_ready (and with
        # warmpool.is_warm): a completed transcript job is not enough for a
        # translation task, which also needs deliverable_revision set. Using
        # job__status='completed' alone sorted transcript-only translations
        # into the front-page "ready" group, pushing genuinely ready tasks
        # onto later pages and making the on-page ready count read low even
        # though the site-wide ready total was correct. ready_for_work_q()
        # is the shared definition -- see its docstring for why this needed
        # one.
        from django.db.models import Case, IntegerField, Value, When
        return queryset.annotate(
            _readiness=Case(
                When(ready_for_work_q(), then=Value(0)),
                default=Value(1), output_field=IntegerField(),
            ),
        ).order_by('_readiness', 'duration_seconds', '-priority', 'created_at')

    def get_object(self):
        """
        `claim` looks the task up WITHOUT the visibility filter.

        `visible_tasks_for` drops a task the instant someone else claims
        it, so a worker who lost a race would 404 rather than being told
        they lost. Object permission still runs (TaskAccessPermission
        allows the attempt only if the task is not reserved for a different
        worker), and the service still re-reads under a row lock -- so this
        widens the lookup, not the authorization.
        """
        if getattr(self, 'action', None) == 'claim':
            from django.shortcuts import get_object_or_404
            task = get_object_or_404(Task, pk=self.kwargs['pk'])
            self.check_object_permissions(self.request, task)
            return task
        return super().get_object()

    def get_serializer_class(self):
        if self.action == 'create':
            return TaskCreateSerializer
        return AdminTaskSerializer if is_admin(self.request.user) else TaskSerializer

    def create(self, request, *args, **kwargs):
        if not is_admin(request.user):
            return Response({'error': 'Administrator access is required.'},
                            status=status.HTTP_403_FORBIDDEN)
        serializer = TaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = services.create_task(actor=request.user, **serializer.validated_data)
        return Response(AdminTaskSerializer(task, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        return Response(
            {'error': 'Tasks are changed through their action endpoints.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    partial_update = update

    def destroy(self, request, *args, **kwargs):
        if not is_admin(request.user):
            return Response({'error': 'Administrator access is required.'},
                            status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)

    # ------------------------------------------------------------------ #
    # State actions -- each one delegates to the locking service layer
    # ------------------------------------------------------------------ #

    def _act(self, request, operation, **kwargs):
        """
        Shared wrapper: run a service operation and translate its typed
        failures into the right HTTP status.

        409 for a conflict (the request was fine, the world moved), 403 for
        an ownership failure, 400 for an illegal transition. All three
        return `.safe_message`, never `.detail`.
        """
        task = self.get_object()
        try:
            updated = operation(task.id, **kwargs)
        except services.TaskConflict as exc:
            logger.info('[WORKFORCE] conflict on %s: %s', task.id, exc.detail)
            return Response({'error': exc.safe_message}, status=status.HTTP_409_CONFLICT)
        except services.TaskPermissionDenied as exc:
            logger.warning('[WORKFORCE] denied on %s: %s', task.id, exc.detail)
            return Response({'error': exc.safe_message}, status=status.HTTP_403_FORBIDDEN)
        except states.IllegalTransition as exc:
            logger.info('[WORKFORCE] illegal transition on %s: %s', task.id, exc.detail)
            return Response({'error': exc.safe_message}, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(updated)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def claim(self, request, pk=None):
        """
        Take a task.

        THROUGH EAGLE, NOT AROUND IT. This used to call
        `services.claim_task`, which moves OUR state and nothing else -- so
        pressing Claim marked the task as yours here while leaving it open
        on the platform for anyone else to take. A worker could spend an
        hour on work that was never theirs, and the minutes guard never ran,
        so a task we could not afford looked claimable right up until the
        submission failed.

        `eagle_claim.claim_for_worker` is the path that was already built
        for this: it checks the balance, calls the platform, and moves our
        state ONLY on acknowledgement. A task with no external id -- one
        created here by hand -- still falls through to the plain service
        path inside that function.
        """
        from . import eagle_claim

        profile = worker_profile(request.user)
        if profile is None:
            return Response({'error': 'Only workers can claim tasks.'},
                            status=status.HTTP_403_FORBIDDEN)

        task = self.get_object()
        try:
            updated = eagle_claim.claim_for_worker(
                task.id, worker=profile, actor=request.user)
        except eagle_claim.InsufficientMinutes as exc:
            # 402: the request was valid and the world is fine -- we simply
            # cannot pay for it. Distinct from a conflict so the UI can say
            # something true rather than "someone beat you to it".
            logger.error('[WORKFORCE] minutes exhausted on %s: %s',
                         task.id, exc.detail)
            return Response({'error': exc.safe_message},
                            status=status.HTTP_402_PAYMENT_REQUIRED)
        except eagle_claim.ClaimRefused as exc:
            logger.info('[WORKFORCE] claim refused on %s: %s', task.id, exc.detail)
            return Response({'error': exc.safe_message},
                            status=status.HTTP_409_CONFLICT)
        except states.IllegalTransition as exc:
            logger.info('[WORKFORCE] illegal transition on %s: %s', task.id, exc.detail)
            return Response({'error': exc.safe_message},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response(self.get_serializer(updated).data)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        return self._act(request, services.release_task, actor=request.user,
                         reason=str(request.data.get('reason') or '')[:500])

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        return self._act(request, services.start_task, actor=request.user)

    @action(detail=True, methods=['post'], url_path='begin-review')
    def begin_review(self, request, pk=None):
        return self._act(request, services.begin_review, actor=request.user)

    @action(detail=True, methods=['post'], url_path='ready')
    def ready(self, request, pk=None):
        revision = None
        revision_id = request.data.get('revision')
        if revision_id:
            from apps.transcriptions.models import AIRevision
            revision = AIRevision.objects.filter(pk=revision_id).first()
            if revision is None:
                return Response({'error': 'That revision does not exist.'},
                                status=status.HTTP_400_BAD_REQUEST)
        return self._act(request, services.mark_ready,
                         actor=request.user, revision=revision)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        """
        Records submission. Phase 4 will perform the external call first and
        reach here only on acknowledgement -- the ordering matters, because
        a task marked SUBMITTED that never reached the platform is worse
        than one that failed loudly.
        """
        return self._act(request, services.mark_submitted, actor=request.user,
                         reference=str(request.data.get('reference') or '')[:200])

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def approve(self, request, pk=None):
        return self._act(request, services.mark_approved, actor=request.user,
                         note=str(request.data.get('note') or '')[:500])

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def reject(self, request, pk=None):
        reason = str(request.data.get('reason') or '').strip()
        if not reason:
            return Response({'error': 'A rejection reason is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        return self._act(request, services.mark_rejected, actor=request.user, reason=reason)

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def assign(self, request, pk=None):
        worker_id = request.data.get('worker')
        worker = WorkerProfile.objects.filter(pk=worker_id).first()
        if worker is None:
            return Response({'error': 'That worker does not exist.'},
                            status=status.HTTP_400_BAD_REQUEST)
        return self._act(request, services.assign_task, worker=worker, actor=request.user)

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def unassign(self, request, pk=None):
        return self._act(request, services.unassign_task, actor=request.user)

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def cancel(self, request, pk=None):
        return self._act(request, services.cancel_task, actor=request.user,
                         reason=str(request.data.get('reason') or '')[:500])

    @action(detail=True, methods=['get'])
    def events(self, request, pk=None):
        """
        Audit history for one task. Reachable by anyone who can see the
        task, because a worker being able to check what happened to their
        own work is the point of keeping the log.
        """
        task = self.get_object()
        return Response(TaskEventSerializer(task.events.all()[:200], many=True).data)

    @action(detail=True, methods=['post'], url_path='apply-finding')
    def apply_finding(self, request, pk=None):
        """
        Record that a worker applied ONE specific correction from an
        accuracy-check report -- see ACCURACY_CHECK_FINDING_APPLIED's own
        docstring on the model for why this is a separate, permanent event
        rather than folded into accuracy_check_notes.

        RECORDS ONLY -- DOES NOT TOUCH THE TRANSCRIPT. The actual text
        change goes through the ordinary segment-edit endpoint, exactly
        like any other correction a worker makes by hand; this is the
        audit trail alongside it, not a replacement for it.
        """
        task = self.get_object()
        phrase = str(request.data.get('phrase') or '').strip()[:500]
        suggestion = str(request.data.get('suggestion') or '').strip()[:500]
        if not phrase or not suggestion:
            return Response(
                {'error': 'Provide both phrase and suggestion.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile = worker_profile(request.user)
        is_holder = profile is not None and task.claimed_by_id == profile.id
        if not is_admin(request.user) and not is_holder:
            return Response(
                {'error': 'Only the worker holding this task can do that.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        services.record_event(
            task, TaskEvent.ACCURACY_CHECK_FINDING_APPLIED, actor=request.user,
            message=f'Applied: "{phrase}" -> "{suggestion}"',
            metadata={'phrase': phrase, 'suggestion': suggestion},
        )
        return Response({'recorded': True})


class WorkerViewSet(viewsets.ModelViewSet):
    """Admin worker management. Workers use `/me/` instead."""

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAdmin]
    queryset = WorkerProfile.objects.select_related('user').all()
    serializer_class = AdminWorkerSerializer

    def create(self, request, *args, **kwargs):
        from django.contrib.auth.models import User
        from django.db import transaction

        serializer = WorkerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            user = User.objects.create_user(
                username=data['username'],
                email=data.get('email', ''),
                password=data['password'],
            )
            # The role is set here, not left to be inferred from the
            # profile's existence. Creating a worker means creating a
            # TASKER account; that is the same statement said once.
            Account.objects.create(user=user, role=roles.TASKER)
            profile = WorkerProfile.objects.create(
                user=user,
                worker_code=data['worker_code'],
                display_name=data.get('display_name', ''),
                max_concurrent_tasks=data.get('max_concurrent_tasks', 3),
            )
        logger.info('[WORKFORCE] worker created: %s by %s',
                    profile.worker_code, request.user.username)
        return Response(AdminWorkerSerializer(profile).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        """
        Suspends the profile and releases everything they hold, so their
        queue does not become a black hole that no one else can claim.
        """
        profile = self.get_object()
        profile.status = WorkerProfile.SUSPENDED
        profile.save(update_fields=['status'])

        released = 0
        for task in Task.objects.filter(
            claimed_by=profile, status__in=states.WORKER_HELD_STATES
        ):
            try:
                services.release_task(task.id, actor=request.user,
                                      reason='Worker deactivated.')
                released += 1
            except states.IllegalTransition as exc:
                logger.warning('[WORKFORCE] could not release %s: %s', task.id, exc.detail)
        return Response({'status': profile.status, 'released_tasks': released})

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        profile = self.get_object()
        profile.status = WorkerProfile.ACTIVE
        profile.save(update_fields=['status'])
        return Response(AdminWorkerSerializer(profile).data)


class WorkforceStatsView(views.APIView):
    """
    Raw counters for the admin dashboard.

    Deliberately counts rather than computes: Phase 2's brief is to record
    reliable raw events, not to build analytics. Averages come from the
    timestamps already on Task, so nothing here needs a separate rollup
    table that could drift from the source.
    """

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAdmin]

    def get(self, request):
        by_status = dict(
            Task.objects.values_list('status')
            .annotate(n=Count('id')).values_list('status', 'n')
        )
        workers = []
        for profile in WorkerProfile.objects.select_related('user'):
            owned = Task.objects.filter(claimed_by=profile)
            workers.append({
                'worker_code': profile.worker_code,
                'display_name': profile.display_name or profile.user.username,
                'status': profile.status,
                'held': owned.filter(status__in=states.WORKER_HELD_STATES).count(),
                'submitted': owned.filter(submitted_at__isnull=False).count(),
                'approved': owned.filter(status=states.APPROVED).count(),
                'rejected': owned.filter(status=states.REJECTED).count(),
                'last_activity_at': profile.last_activity_at,
            })
        return Response({
            'totals': {
                'tasks': Task.objects.count(),
                'by_status': by_status,
                'unassigned': Task.objects.filter(
                    status__in=states.CLAIMABLE_STATES, claimed_by__isnull=True
                ).count(),
                'workers': WorkerProfile.objects.count(),
                'active_workers': WorkerProfile.objects.filter(
                    status=WorkerProfile.ACTIVE, user__is_active=True
                ).count(),
            },
            'workers': workers,
            'recent_events': TaskEventSerializer(
                TaskEvent.objects.select_related('actor')[:50], many=True
            ).data,
        })


class AccountViewSet(viewsets.ModelViewSet):
    """
    Accounts and their roles. Administrator only.

    WHY ROLE CHANGES ARE AN ACTION, NOT A PATCH. Changing someone's role has
    consequences that a field write cannot express: promoting to tasker has
    to produce a worker profile, and demoting a tasker who is holding work
    would orphan it. Both rules live in one place here, for the same reason
    task transitions go through the service layer instead of `PATCH status`.
    """

    authentication_classes = AUTH_CLASSES
    permission_classes = [IsAdmin]
    queryset = Account.objects.select_related('user').all()
    serializer_class = AccountSerializer
    http_method_names = ['get', 'post', 'head', 'options']

    def create(self, request, *args, **kwargs):
        from django.contrib.auth.models import User
        from django.db import transaction

        serializer = AccountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        role = data['role']

        with transaction.atomic():
            user = User.objects.create_user(
                username=data['username'],
                email=data.get('email', ''),
                password=data['password'],
                # An ADMIN account gets the Django staff flag too, so the
                # role and the flag agree from the moment it is created
                # rather than drifting apart later.
                is_staff=(role == roles.ADMIN),
            )
            account = Account.objects.create(user=user, role=role)
            if role == roles.TASKER:
                WorkerProfile.objects.create(
                    user=user,
                    worker_code=data['worker_code'].strip(),
                    display_name=data.get('display_name', ''),
                    max_concurrent_tasks=data.get('max_concurrent_tasks', 3),
                )

        logger.info('[WORKFORCE] account created: %s as %s by %s',
                    user.username, role, request.user.username)
        return Response(AccountSerializer(account).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='set-password')
    def set_password(self, request, pk=None):
        """
        Give somebody a new password because they forgot theirs.

        WHY THIS IS NOT A SHELL COMMAND. `manage.py worker password` exists
        and works, but it needs SSH to the server, and the person who issues
        credentials on a six-person team should not need a terminal to
        answer "I have forgotten my password" -- which is the single
        commonest administrative request there is.

        THE ADMIN CHOOSES THE PASSWORD; NOTHING IS MAILED. There is no
        outbound mail on this deployment, so a reset link would be a feature
        that silently does nothing. The admin sets a temporary one and tells
        the person by whatever channel they already use.

        SESSIONS ARE ENDED TOO. If the reset is because an account was
        compromised, leaving the intruder's session alive makes the reset
        pointless -- and if it is an ordinary forgotten password, ending the
        old sessions costs the person nothing, because they cannot get into
        one anyway.
        """
        from django.contrib.auth import update_session_auth_hash
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError

        account = self.get_object()
        password = str(request.data.get('password') or '')
        if not password:
            return Response({'error': 'A password is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_password(password, account.user)
        except ValidationError as exc:
            return Response({'error': ' '.join(exc.messages)},
                            status=status.HTTP_400_BAD_REQUEST)

        account.user.set_password(password)
        account.user.save(update_fields=['password'])

        # Changing your OWN password would otherwise sign you out of the
        # page you are standing on, which reads as the app breaking.
        if account.user_id == request.user.id:
            update_session_auth_hash(request, account.user)

        logger.info('[WORKFORCE] password reset for %s by %s',
                    account.user.username, request.user.username)
        return Response({'status': 'ok', 'username': account.user.username})

    @action(detail=True, methods=['post'], url_path='set-role')
    def set_role(self, request, pk=None):
        from django.db import transaction

        account = self.get_object()
        new_role = str(request.data.get('role') or '').strip()
        if new_role not in roles.ALL_ROLES:
            return Response({'error': 'That is not a valid role.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Nobody may remove their own administrator role. One admin
        # demoting themselves by accident locks the whole team out of task
        # approval, and the only way back is a shell.
        if account.user_id == request.user.id and new_role != roles.ADMIN:
            return Response(
                {'error': 'You cannot change your own administrator role.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_role == account.role:
            return Response(AccountSerializer(account).data)

        profile = WorkerProfile.objects.filter(user=account.user).first()

        # Leaving the workforce while holding work would strand it: the
        # tasks stay claimed by someone who can no longer reach them.
        # Refuse and say what to do, rather than silently releasing work
        # the admin did not ask to touch.
        if account.role == roles.TASKER and new_role != roles.TASKER and profile:
            held = Task.objects.filter(
                claimed_by=profile, status__in=states.WORKER_HELD_STATES,
            ).count()
            if held:
                return Response(
                    {'error': f'That worker still holds {held} task'
                              f'{"" if held == 1 else "s"}. Suspend them first, '
                              f'which releases their work, then change the role.'},
                    status=status.HTTP_409_CONFLICT,
                )

        if new_role == roles.TASKER and profile is None:
            code = str(request.data.get('worker_code') or '').strip()
            if not code:
                return Response({'error': 'A worker code is required to make someone a tasker.'},
                                status=status.HTTP_400_BAD_REQUEST)
            if WorkerProfile.objects.filter(worker_code__iexact=code).exists():
                return Response({'error': 'That worker code is already in use.'},
                                status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            previous = account.role
            account.role = new_role
            account.save(update_fields=['role', 'updated_at'])

            if new_role == roles.TASKER and profile is None:
                WorkerProfile.objects.create(
                    user=account.user,
                    worker_code=str(request.data.get('worker_code')).strip(),
                    display_name=str(request.data.get('display_name') or '')[:120],
                )

            # Keep the Django staff flag in step with the role, so the two
            # admin signals cannot disagree (see permissions.is_admin).
            should_be_staff = (new_role == roles.ADMIN)
            if account.user.is_staff != should_be_staff and not account.user.is_superuser:
                account.user.is_staff = should_be_staff
                account.user.save(update_fields=['is_staff'])

        logger.info('[WORKFORCE] role changed: %s %s -> %s by %s',
                    account.user.username, previous, new_role, request.user.username)
        return Response(AccountSerializer(account).data)

# Operations Runbook

Practical "if this happens, do this" reference for problems that have actually
come up while running this platform on a single Windows dev machine acting as
the server (including behind a Cloudflare tunnel for remote testers). Every
command below has been run for real against this project — none of this is
theoretical.

Two shells are used throughout: **PowerShell** for anything touching Windows
processes/ports, and **Bash** (Git Bash) for `curl` and Django management
commands. Use whichever the command block says.

---

## 1. "Everything feels slow / stalled" — check for leaked processes first

### Symptom
Jobs take far longer than they should, requests feel sluggish, or a job looks
"stuck" even though nothing in the logs explains why.

### Why this happens
Every time the dev server autoreloads (which happens on *every file save*
while Claude or you are editing backend code), it spawns a fresh worker
process. If a previous server session was ever stopped by **closing the
terminal window** instead of pressing Ctrl+C, the old process can be left
running in the background — sometimes for days — silently competing for CPU
with the server you're actually using. This has happened for real: two
forgotten servers from an older project were found still running after
**10–12 days**, having burned over **15 hours of CPU time** combined.

### How to check
```powershell
# Lists every running Python process with when it started and how much CPU
# time it has burned. A StartTime more than a few hours old, or a CPU value
# in the thousands of seconds, is a red flag -- a healthy just-started dev
# server should show a small, growing CPU number and a recent StartTime.
Get-Process | Where-Object { $_.ProcessName -match 'python' } |
  Select-Object Id, ProcessName, StartTime, CPU
```

```powershell
# Cross-references each PID above with its actual command line, so you can
# see WHICH project and port each one belongs to -- essential before killing
# anything, since more than one project may share this machine.
Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' } |
  Select-Object ProcessId, ParentProcessId, CreationDate, CommandLine |
  Format-List
```

```powershell
# Confirms which PID is ACTUALLY listening on a given port right now (the
# one that matters) -- a project can have multiple leftover processes in the
# table above, but only one of them is really serving traffic on any given
# port. Replace 8002 with whatever port you're checking.
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -eq 8002 } |
  Select-Object LocalPort, OwningProcess
```

### How to fix it
```powershell
# Kills a specific leaked process by PID. -Force skips any "are you sure"
# prompt. Only kill PIDs you've confirmed (via the commands above) are NOT
# the one currently listening on the port you still need.
Stop-Process -Id <PID_HERE> -Force -ErrorAction SilentlyContinue
```

```powershell
# After killing anything, ALWAYS re-check that the server you still need is
# still alive and responding before moving on.
curl -s -o /dev/null -w "HTTP %{http_code} in %{time_total}s`n" http://127.0.0.1:8002/api/v1/projects/
```

> **Known risk, seen for real:** killing an *ancestor* watcher process in a
> multi-generation reloader chain (e.g. a two-day-old process that a fresh
> worker's parentage traces back through) can break that chain and take the
> whole live server down with it, even though it wasn't the one you targeted.
> If your live server stops responding right after a cleanup, don't panic —
> just restart it (see §4) and verify with the `curl` check above.

### How to prevent it
**Always stop a dev server with Ctrl+C in its own terminal window.** Closing
the window, closing the terminal tab, or losing an SSH/remote session can
detach the process without killing it. If you're ever unsure whether an old
session is still running, run the check commands above before starting a new
one — it takes five seconds and would have caught every leak seen so far.

---

## 2. A transcription job looks permanently "stuck"

### Symptom
A job sits at "Downloading..." or "Transcribing..." forever, with no
progress, sometimes for days.

### Why this happens (and why it's now mostly self-fixing)
Every job runs on a background thread inside the server process. If the
server restarts while a job is mid-flight (an autoreload from a code edit, a
crash, a manual restart), that thread just dies with the old process — and
until this was fixed, nothing ever went back and marked the job `failed`, so
it sat "in progress" forever with literally nothing working on it.

**This is now fixed automatically.** Every time the server starts (see
`apps/transcriptions/apps.py` → `TranscriptionsConfig.ready()` →
`job_cleanup.recover_orphaned_jobs()`), any job/translation/discovery task
still sitting in an in-progress status is assumed orphaned (there is no other
process that could still be working on it) and is marked `failed` with a
clear reason, temp files cleaned up, and the row kept visible in history —
you should see a line like this in the startup log when it happens:

```
Startup recovery: marked 11 orphaned job(s), 0 orphaned translation(s), and 0 orphaned discovery task(s) as failed (interrupted by a previous server run).
```

### How to check for stuck jobs manually
```bash
# Run from backend/. Lists every job currently sitting in an in-progress
# status and how long it's been there. Anything more than a few minutes old
# (matching how long your longest real job normally takes) is suspicious --
# it should get auto-recovered on the next server restart, but you can check
# right now without restarting.
cd backend
../.venv/Scripts/python.exe manage.py shell -c "
from apps.transcriptions.models import TranscriptionJob
from django.utils import timezone
now = timezone.now()
for j in TranscriptionJob.objects.filter(status__in=['queued','downloading','transcribing']):
    print(f'{j.id} | status={j.status} | age={(now-j.created_at).total_seconds():.0f}s | {j.source_url or j.file_path}')
"
```

```bash
# Manually triggers the same recovery the server runs on startup, WITHOUT
# needing to actually restart the server. Use this if you found stuck jobs
# above and don't want to wait for/force a restart.
cd backend
../.venv/Scripts/python.exe manage.py shell -c "
from apps.transcriptions.job_cleanup import recover_orphaned_jobs
print(recover_orphaned_jobs())
"
```

### If a job is genuinely still running but you just want it to stop
Don't restart the server for this — use the **Cancel** button in the UI (or
`POST /api/v1/transcriptions/<id>/cancel/`). It stops the download/transcribe
loop cooperatively within about a second and cleans up its own temp files,
without affecting any other job or requiring a restart.

---

## 3. Regression testing — run this after any backend or frontend change

### Backend
```bash
# Run from backend/. Runs the full Django test suite (models, views, tasks,
# pipeline, cancellation, cleanup, uploads, orphan recovery -- everything).
# A healthy run ends with "OK" and a test count; any FAIL/ERROR lines above
# that need investigating before you consider a change safe.
cd backend
../.venv/Scripts/python.exe manage.py test apps.transcriptions
```

```bash
# Same as above but only one test class/method -- much faster when you're
# iterating on a single fix. Replace the dotted path with whichever
# class (or Class.method_name) you're working on.
../.venv/Scripts/python.exe manage.py test apps.transcriptions.tests.OrphanedJobRecoveryTestCase
```

```bash
# The workforce app (accounts, roles, tasks, the state machine, the audit
# log, the performance report and the live-update socket) is a SEPARATE
# label -- the command above does not include it.
#
# --noinput matters: if a previous run was interrupted, the test database is
# still there and the runner otherwise stops to ask whether to delete it,
# which fails with EOFError when nothing is attached to stdin.
../.venv/Scripts/python.exe manage.py test apps.workforce --noinput
```

#### "being accessed by other users"

```
django.db.utils.OperationalError: database "test_transcription_platform"
is being accessed by other users
DETAIL:  There is 1 other session using the database.
```

This is **not** a broken test, and it appears in two different places —
check which before doing anything:

| Where | What it means |
|---|---|
| **After** `Ran N tests / OK` | The suite passed. Teardown could not drop the test database. Harmless to this run; it will break the *next* one. |
| **Before** any test output | The previous run's leftover database could not be dropped, so this run never started. |

> **Read the whole output, not the tail.** The teardown traceback is ~20
> lines, so `... | tail -15` scrolls `Ran N tests` and `OK` off the top and
> a passing run looks like a failure. Filter instead of truncating:
> `manage.py test apps.transcriptions --noinput 2>&1 | grep -E "^Ran |^OK$|^FAILED|^FAIL:|^ERROR:"`.

The cause is the same in both cases: the pipeline runs in daemon threads,
Django opens one database connection per thread, and nothing closes them
(see the concurrency notes in the audit) — so a connection outlives the run
that opened it.

Clear it first, in the same command, so nothing can reconnect in between:

```bash
# Run from backend/. Terminates any leftover sessions, drops the test
# database, and only then starts the suite.
../.venv/Scripts/python.exe -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','transcription_proj.settings')
django.setup()
from django.db import connection
connection.ensure_connection(); connection.connection.autocommit = True
with connection.cursor() as c:
    c.execute(\"select pg_terminate_backend(pid) from pg_stat_activity where datname like 'test_%'\"); c.fetchall()
    c.execute('DROP DATABASE IF EXISTS \"test_transcription_platform\"')
print('test database cleared')
" && ../.venv/Scripts/python.exe manage.py test apps.workforce --noinput
```

**Never run two suites at once** — they share one test database name and
will fight over it.

> **Reading the duration:** Django reports *wall clock*. One overnight run
> printed `Ran 137 tests in 31278.153s` (8.7 hours) purely because the
> machine slept partway through — the Windows event log showed sleep at
> 00:13 and resume at 08:44. Real compute was ~9 minutes. Check
> `Get-WinEvent -ProviderName Microsoft-Windows-Kernel-Power` before
> believing a suite suddenly got slower.

### Frontend
```bash
# Run from frontend/. A production build catches syntax errors, bad imports,
# and JSX mistakes that "it looks fine in the dev server" can miss. A clean
# run ends with "✓ built in Xs" and no errors -- this does NOT run the app,
# it only proves the code compiles.
cd frontend
npm run build
```

```bash
# Component tests (vitest + testing-library). These cover the parts of the
# UI where being wrong is expensive: which task actions are offered, what a
# failed sign-in reveals, and how the API client reports errors. A clean run
# ends with "Tests N passed".
npm test
```

### What counts as "safe to consider done"
- Backend suite: **every** test passes, not just the ones related to your
  change — several fixes in this project turned out to touch shared code
  paths (e.g. the file-download retry logic, the serializer's read-only
  fields) used by features that looked unrelated at first glance.
- Frontend: the build must complete with no errors, **and** `npm test` must
  pass. A clean build only proves the code compiles. The tests cover the
  decisions that are expensive to get wrong (which actions a task offers,
  what a failed sign-in reveals); everything else — layout, live updates,
  the transcription studio itself — still needs a look in a real browser.

---

## 3a. "I can't get in" — there are no accounts by default

### Symptom
The app opens on a sign-in screen and no username works. `Invalid
credentials.` for everything you try.

### Why this happens
As of Phase 3 the whole frontend is behind authentication. A fresh database
has no worker accounts at all, and the sign-in screen deliberately refuses to
say whether a username exists — so "no account" and "wrong password" look
identical from the outside. They are meant to.

### Fix
```bash
# Run from backend/. Creates the Django user and the worker profile together
# and prompts for the password twice -- it is never passed as an argument,
# which would leave it in the shell history and the process list.
#
# The password must satisfy Django's validators (12+ characters, not all
# numeric, not a common password). A short one is rejected here, not
# silently accepted and then refused at the login screen.
cd backend
../.venv/Scripts/python.exe manage.py create_worker ana --code W-01 --name "Ana"
```

```bash
# An administrator sees the admin dashboard and can approve, reject, assign
# and cancel. --admin sets is_staff, and defaults the worker code to ADMIN.
../.venv/Scripts/python.exe manage.py create_worker boss --admin --name "Boss"
```

### The three kinds of account
Every user has exactly one **role**, stored on `workforce_account`:

| Role | Sees | Needs a worker code |
|---|---|---|
| `admin` | Everything, including the admin dashboard | no |
| `tasker` | The task queue and their own work | yes |
| `tool` | The transcription studio only | no |

`create_worker` makes taskers and admins. A **tool-only** account — someone
allowed to use the studio but not part of the workforce — is created from the
admin UI at **Accounts → Add account**, because it needs no worker code and
no profile.

Role is separate from status on purpose: **suspending a tasker does not change
their role.** They stay a tasker who currently cannot work, rather than
silently becoming a tool user with studio access.

### Live task updates: nothing extra to run
The worker and admin screens subscribe to `ws/workforce/` for instant task
updates. `daphne` sits first in `INSTALLED_APPS`, so `manage.py runserver`
already serves ASGI and the socket upgrades normally — there is no separate
process to start.

If the socket does not connect (an old browser, a proxy that strips
upgrades), **nothing breaks**: every screen also polls every 15–20 seconds,
so the cost is a few seconds of latency. The socket carries only a task id
and a status; the client always re-fetches through the REST API, which is
what applies the visibility rules. Do not add a "reconnect" troubleshooting
step for it — it backs off and retries on its own.

---

## 3b. "CSRF Failed: Origin checking failed"

### Symptom
Signing in — usually the *second* time, or after coming back to the tab —
fails with:

```
CSRF Failed: Origin checking failed - http://localhost:3000 does not match
any trusted origins.
```

Nothing is wrong with the password.

### Why this happens
Django compares the browser's `Origin` header against the request's host on
every unsafe request that reaches its CSRF machinery. In development those can
never match on their own: the browser's origin is the Vite dev server
(`:3000`) while the request arrives at Django (`:8002`), and Vite's
`changeOrigin: true` rewrites the `Host` header on the way through.

That only bites when a request authenticates by **session** instead of by
token — which is exactly what a repeat sign-in did, because a leftover session
cookie made DRF authenticate the request before it ever read the credentials.

### Fix (already applied)
Two changes, both in the repo:

1. `LoginView.authentication_classes = []` — an endpoint whose job is to
   *establish* an identity must not read one the caller already has.
2. `CSRF_TRUSTED_ORIGINS` in `settings.py`, defaulting to `localhost` and
   `127.0.0.1` on ports 3000/3001/8002.

**`localhost` and `127.0.0.1` are different origins to a browser** — separate
cookies, separate `localStorage`. Both are listed because whichever one you
type has to work, and testing only one is how this was missed.

### If you serve the app from anywhere else
A tunnel host or a real domain needs adding, or you will see the same error
there. Set it in the environment rather than editing settings:

```bash
# backend/.env -- comma-separated, scheme included, no trailing slash
CSRF_TRUSTED_ORIGINS=https://your-tunnel.trycloudflare.com,https://app.example.com
```

Setting the variable **replaces** the development defaults, so include the
local origins too if you still want them.

---

## 4. Restarting the backend server cleanly

```powershell
# Check what's currently listening on the port before doing anything --
# tells you whether a server is even running, and which PID owns it.
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -eq 8002 } |
  Select-Object LocalPort, OwningProcess
```

```powershell
# If something's already listening and you want it gone before starting a
# fresh one: stop it by PID (from the command above).
Stop-Process -Id <PID_HERE> -Force -ErrorAction SilentlyContinue
```

```bash
# Start the server fresh. Run from backend/. Watch the first few lines of
# output -- you should see the "Startup recovery: marked N orphaned job(s)"
# line (see §2) followed by "Watching for file changes with StatReloader"
# and finally "Listening on TCP address 127.0.0.1:8002". If it exits
# immediately instead, something crashed on startup -- read the traceback,
# don't just retry blindly.
cd backend
../.venv/Scripts/python.exe manage.py runserver 8002
```

```bash
# Quick health check once it's up -- confirms the server is actually
# accepting and answering requests, not just that the process exists.
curl -s -o /dev/null -w "HTTP %{http_code} in %{time_total}s`n" http://127.0.0.1:8002/api/v1/projects/
```

---

## 5. Diagnosing a genuinely slow (not stuck) job from the logs

Not every slow job is a bug. Two things are known, expected, one-time-ish
costs that can look alarming in the logs but are normal:

- **First Whisper job after a server (re)start** pays a real, one-time cost
  (often 30–90s) loading the model into memory — logged explicitly as its own
  `[PROFILE] stage=whisper_model_load` entry so it's never confused with
  actual per-file transcription time. Every job after that, in the same
  server process, is instant here.
- **A slow origin server for direct file downloads** (not yt-dlp) is capped
  at a 45s wall-clock deadline before falling back to yt-dlp — if you see
  `stage=direct_http_download duration=45.0s` immediately followed by a
  yt-dlp retry, that's the safety net working as designed, not a hang.

If a job is slow for a reason other than these two, check §1 first — CPU
contention from a leaked process is the single most likely cause found so
far on this machine.

---

## 6. Enabling real speaker diarization

The "Enable Speaker Diarization" checkbox uses a real model
(`pyannote/speaker-diarization-3.1`, via `pyannote.audio`) — it is off by
default and silently falls back to placeholder speaker labels if not set up,
so skipping this section costs nothing except not having real diarization.

### One-time setup
```bash
# Run from backend/. Installs the diarization library -- this can take a
# while and pull a large dependency tree (it reuses the torch install
# faster-whisper already needs). Safe to run again if it was interrupted.
cd backend
../.venv/Scripts/python.exe -m pip install "pyannote.audio>=3.1,<3.2"
```

1. Create a free account at huggingface.co if you don't have one.
2. Visit https://huggingface.co/pyannote/speaker-diarization-3.1 and
   https://huggingface.co/pyannote/segmentation-3.0, and accept each
   model's license terms (a button on the page — no payment, just an
   acknowledgement).
3. Create an access token: huggingface.co → Settings → Access Tokens →
   New token (read access is enough).
4. Set it as an environment variable before starting the server (any ONE
   of these three names works — `core.services.diarization` checks all of
   them):
```powershell
# PowerShell -- set for the current terminal session only. Set it before
# running `manage.py runserver`, not after.
$env:HF_TOKEN = "hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### How to tell if it's working
Check for diarization requested but not applied:
```bash
cd backend
../.venv/Scripts/python.exe -c "
from core.services.diarization import SpeakerDiarizer
print('configured:', SpeakerDiarizer.is_configured())
"
```
`False` means either the library isn't installed or no token is set --
diarization-enabled jobs will still complete normally, just with the old
placeholder labels, and the server log will say so explicitly:
`"diarization unavailable or failed; using placeholder speaker labels."`

The model downloads (a few hundred MB) on the *first* diarization-enabled
job after this is configured, then stays cached — subsequent jobs don't
re-download it. That first job pays this cost the same way the first
Whisper job of any server session pays its own one-time model-load cost
(see §5).

---

## 7. A page transcribes in the browser but fails here ("Unsupported URL")

### Symptom
A URL you can play audio from in Chrome fails the job with
`ERROR: Unsupported URL: ...` and, just before it,
`No direct embedded audio link found. Falling back to yt-dlp's generic
extractor.`

### Why this happens
The site is a client-rendered application (Vue/React/Angular). The server
sends only an empty shell; the player -- and the media URL -- are created
later by JavaScript. `curl` and the static scraper see the shell, which
genuinely contains no audio, so there is nothing to find. Often the
`<audio>` element ends up inside a shadow root with a `blob:` src, which
is a handle to bytes already in the browser's memory and cannot be fetched
from outside it.

The `dynamic_discovery` stage handles this by running the page in a
headless browser just long enough to see WHICH HTTP request supplies the
media, then handing that URL back to the normal download path. It is the
LAST stage tried, so nothing that already works is affected by it.

### One-time setup
```bash
# Run from the repo root. Two steps: the Python package, then the browser
# binary it drives. pip alone is not enough -- the second command is what
# downloads Chromium (~150MB), and skipping it leaves the stage disabled.
.venv/Scripts/python.exe -m pip install "playwright>=1.45,<2.0"
.venv/Scripts/python.exe -m playwright install chromium
```

### How to tell if it's working
```bash
# Run from backend/. Reports whether the stage can run at all. This is a
# cheap check -- it does not launch a browser.
cd backend
../.venv/Scripts/python.exe -c "
from core.services import dynamic_discovery
print('available:', dynamic_discovery.is_available())
"
```
`False` means the package isn't installed or the feature is switched off;
such URLs then fail exactly as they did before this stage existed --
nothing else in the pipeline is affected.

To confirm the browser binary is also present (the part `pip install`
does NOT do), launch it once:
```bash
cd backend
../.venv/Scripts/python.exe -c "
from core.services.dynamic_discovery.pool import POOL
print('browser launched:', POOL.submit(lambda b: b.version, timeout=120))
"
```
A `DiscoveryUnavailable: Could not launch headless Chromium` here means
`playwright install chromium` was never run.

### If discovery runs but finds nothing
The server log narrates every attempt. Look for these lines:

- `Skipping dynamic media discovery: not a js-app (signals: ...).` — the
  gate decided the page isn't a JS app, so no browser was launched. If you
  know it is one, set `DYNAMIC_DISCOVERY_FORCE=1` to bypass the heuristic.
- `A blob-backed player was found but the request that created it was not
  observed within the budget` — a player exists but its media request
  happened outside the window. Raise `DYNAMIC_DISCOVERY_SETTLE_SECONDS`
  (default 8) and `DYNAMIC_DISCOVERY_TOTAL_DEADLINE_SECONDS` (default 25).
- `No media source found for ...` — nothing media-shaped was requested at
  all. Usually means the page needs a login: paste the session cookie into
  the job's authentication field, exactly as for any other gated URL.

For media on an internal/LAN host, set
`DYNAMIC_DISCOVERY_ALLOW_PRIVATE_HOSTS=1` — private and loopback
addresses are refused by default, since this stage executes untrusted
third-party JavaScript.

All settings are documented in `backend/.env.example`.

### Testing it
```bash
# Run from backend/. Exercises the whole stage against a local fixture
# server covering every media-delivery shape, real browser included.
# These SKIP rather than fail when playwright isn't installed.
cd backend
../.venv/Scripts/python.exe manage.py test \
  apps.transcriptions.tests.DynamicDiscoveryBrowserTestCase -v 2
```

### If discovery FINDS the media but the job still fails

This is a different failure from the ones above, and it has its own log
signature: discovery reports success and the job dies immediately after.

```
[DYNAMIC DISCOVERY] Media source found in 6.41s: https://host/api/user/tasks/3927/source (audio/mpeg, ...)
Media Discovery classified URL as: 'generic'
ERROR: Unsupported URL: https://host/api/user/tasks/3927/source
RuntimeError: Audio extraction or download failed.
```

`classified URL as: 'generic'` on a URL discovery just proved is audio is
the tell. It means the discovered URL was handed back to the download path
and then MISCLASSIFIED there, so proven audio went to the page-scraping
branch instead of being downloaded.

The cause is a URL with no file extension. `MediaDiscovery.classify()` can
read `.mp3` straight off the path, but `/api/user/tasks/3927/source` has
nothing to read, so it falls back to sniffing the response's
`Content-Type`. Sniffing a session-gated endpoint anonymously gets a login
page back -- `text/html` -- and `text/html` is not media.

Both halves are now handled and neither needs configuration:

- The sniff carries the job's authentication (the cookie or token pasted
  into the job's authentication field), so a gated endpoint answers with
  its real content type instead of a login page.
- Discovery forwards the `Content-Type` the browser already OBSERVED, so
  the common case costs no request at all -- the observed type is better
  evidence than a sniff can produce anyway.

If you see this signature again, the job's authentication field is the
first thing to check: without it there is nothing to authenticate the
sniff with, and the endpoint will keep answering with a login page.

Known gap: only `audio/*` and manifest types are recognised this way. A
discovered `video/*` source still classifies as `generic`, matching how
the pipeline already treats `.mp4` URLs. Video-serving authenticated SPAs
are therefore not covered yet.

### Proving the whole chain on a real authenticated URL

`scripts/verify_eagle_handoff.py` walks the chain one boundary at a time
and names the stage that fails, instead of collapsing everything into
"dynamic discovery failed":

```
A browser launch          F authenticated source request
B authentication          G media response/body
C SPA load                H FFmpeg input
D task API                I FFmpeg extraction
E source discovery        J handoff through the real product entry point
```

```powershell
# Run from backend/. The cookie is read from the environment and is never
# printed, logged, or written to disk by the script. Any cookie that has
# appeared in a terminal transcript should be treated as compromised and
# refreshed.
$env:EAGLE_COOKIE = '<session cookie>'
..\.venv\Scripts\python.exe scripts\verify_eagle_handoff.py
..\.venv\Scripts\python.exe scripts\verify_eagle_handoff.py https://host/tasks/4001
```

Phase 1 also reports the second at which the media request appeared. If
that number is larger than the settle window it says so explicitly --
that measurement, not guesswork, is how
`DYNAMIC_DISCOVERY_SETTLE_SECONDS` should be chosen for a slow-loading
app. Prefer setting it per-run in the environment over raising the
default, which would add latency for every other site.

Stage I checks the WAV is larger than one second of 16 kHz mono audio
(32,000 bytes). A near-empty WAV means FFmpeg wrote a header and got no
usable audio -- treat that as a failure even though the file exists.


## 8. The two knobs that decide warm-transcript quality and its cost

Warming does not just fetch audio and transcribe it. Before a tasker can
claim a task it also brings the transcript up to Eagle's own rubric, which
is the difference between a reviewer correcting a machine transcript and a
reviewer proof-reading something already close to submittable.

Both knobs are environment variables, read in `transcription_proj/settings.py`.

### `EAGLE_TRANSCRIPTION_STANDARD` — which rubric (default `edited_verbatim`)

| Value | What it means |
|---|---|
| `edited_verbatim` | Fillers and false starts removed, contractions expanded, slang standardized, grammar corrected. Eagle's general transcription work. |
| `strict_verbatim` | Word-for-word and sound-for-sound. Every one of the corrections above is itself a defect. Legal / court-admissible work. |

**Getting this wrong is not a style disagreement.** A transcript that is
flawless under one standard is a guaranteed rejection under the other, which
is why it is a setting rather than a constant. It drives three things at
once: the instruction sent to the model during warming, the checks the
submission gate runs, and what the gate reports as a defect.

### `EAGLE_WARM_QUALITY_PROFILE` — how hard the gate looks (default `eagle_web`)

`eagle_web` is the zero-tolerance profile: it blocks on MEDIUM findings, not
just critical ones, and refuses to sign off on a local-model fallback.

**It is also the most expensive** — roughly three model requests per task
against one. Set it to `none` to warm without a gate when budget matters
more than the first-pass acceptance rate:

```
EAGLE_WARM_QUALITY_PROFILE=none
```

Warming still applies the standard; only the gate stops running. Text the
gate refuses is never promoted to the worker's starting point — the raw
transcript is kept instead, because confidently wrong text is harder to
review than obviously rough text.

### Checking what a document would be flagged for, without submitting

```python
# backend/, in a shell
from core.services.ai_assist.quality import run_transcript_checks
for issue in run_transcript_checks(open('doc.txt').read(), 'edited_verbatim'):
    print(issue.severity, issue.rule, issue.problem)
```

Zero provider requests, so this is free to run as often as you like.


## 9. A running poller does NOT pick up your code changes

Python does not reload modules in a live process. `eagle_cycle --loop` holds
whatever the code said when it started, and it will keep holding it for
hours while you edit the file and watch the same failure repeat.

This cost three separate rounds of confusion on one afternoon: a session-
rotation fix, a request-pacing fix, and then the pacing fix again — each
time the log kept showing the old behaviour, each time because the loop was
still running the old code.

**After ANY change under `backend/`, restart the loop.**

```bash
# systemd
sudo systemctl restart eagle-cycle

# Windows scheduled task
Stop-ScheduledTask EagleCycle; Start-ScheduledTask EagleCycle
```

### How to tell whether a fix is actually live

Compare the loop's start time against the file's modification time. If the
file is newer, the loop is running code you have already replaced:

```bash
systemctl show eagle-cycle -p ExecMainStartTimestamp
stat -c '%y %n' backend/core/services/ai_assist/providers.py
```

The symptom to watch for: you fix something, the same error keeps appearing,
and you start doubting the fix. Check the start time before doubting the
fix.

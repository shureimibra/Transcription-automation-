# Before deployment — decisions already made, and what to test on the box

Written 1 September 2026, at the end of a live working day. Everything here
was measured on real tasks, not estimated. Read this before provisioning the
server; several decisions are already made and one of them needs a test that
can only be run from the box itself.

---

## 1. The bottleneck is being first, not being fast

Measured across one day:

| | posted | left after ~4h | taken by other people's taskers |
|---|---|---|---|
| transcription | 45 | 2 | 23% |
| translation | 44 | 0 | **75%** |

A previous night: ~20 jobs posted, all gone within 2 hours.

A warm 5-minute task is about **10 minutes of human work** — 3.5 minutes
listening at 1.5×, a few minutes reviewing, one to preview and submit. So
4–5 tasks/hour per person is realistic, and **six taskers is ~30 tasks/hour**.

**Your team can absorb an entire drop in under an hour.** Throughput is not
the constraint. Discovery and claim speed are.

Everything below follows from that.

---

## 2. The scheduler — needed now, replaced by the service later

Nothing currently polls Eagle. Tasks were discovered on the working day only
because `eagle_cycle` was run by hand; the previous night's 20 jobs were
never seen at all — not lost to a race, never fetched.

- **Now, on Windows:** `deploy/install-windows-task.ps1`, run elevated. It is
  explicitly a stop-gap.
- **On the box:** `deploy/eagle-cycle.service`. Not needed *in addition* —
  it replaces the scheduled task entirely.

**Why the service is better than a cron-style task:** warming loads roughly
750 MB of ASR models. A task that launches a fresh process every few minutes
reloads them every time; `--loop` loads once and stays up.

Only install the Windows task if deployment is more than a day or two away.

### Two things learned running it for real (2026-09-01)

**`--loop` means code changes need a restart.** The task runs ONE long-lived
process, so a fix to `warmpool.py` does not reach it until the task is
ended and started again. This cost an afternoon: `select_candidates` was
fixed at 17:09 and the loop kept using the version it had loaded at 15:24,
warming nothing while the log looked healthy. After any deploy:

```powershell
schtasks /End /TN EagleCycle
schtasks /Run /TN EagleCycle
```

Credentials are the exception — `get_credential()` is called per operation,
so a fresh cookie is picked up without a restart.

**FFmpeg is invisible to SYSTEM.** WinGet installs it under the installing
user's `AppData`, and the scheduled task runs as SYSTEM, whose
`LOCALAPPDATA` is `C:\Windows\system32\config\systemprofile`. Every warm
failed with *"FFmpeg is not installed or not found on the server"* while
`ffmpeg -version` worked perfectly in an interactive shell. The wrapper now
sets `FFMPEG_DIR` explicitly and `ai_pipeline.py` honours it. On Linux this
disappears — ffmpeg is on the system path for every user.

**When testing this, strip ffmpeg from PATH first.** Checking it from your
own shell finds YOUR copy and proves nothing about SYSTEM's.

---

## 3. The real prize in deploying is the IP, not the scheduling

`cf_clearance` is bound to the IP address that earned it. On carrier NAT the
public address rotates every few hours and silently invalidates the token.
Measured: two consecutive sessions died **3.3 hours** and **2.8 hours** apart.

That is why cookies had to be re-pasted all day. It is not the cookie
handling, and no amount of work on our side fixes it — **only a browser can
earn a new clearance token.**

A static IP removes the problem. Laravel's ~2 hour idle timeout then becomes
the only clock, and the 90-second poll already keeps that at bay.

---

## 4. Auto-login — feasible, but test it FROM THE BOX first

Storing the Eagle email and password server-side is consistent with the
security rule, which is that *workers and the frontend* never see
credentials. They would live under the same Fernet encryption as the cookie
and be entered via stdin, exactly like `eagle_credential --set`.

Probed from the development machine, both login pages are usable:

```
translation    status=200  cloudflare_challenge=False  csrf_token_present=True
transcription  status=200  cloudflare_challenge=False  csrf_token_present=True
```

So the mechanism works: fetch the page for the CSRF token, POST the
credentials, capture the session cookie.

**It fixes one of the two expiry clocks, not both:**

| what expires | auto-login helps? |
|---|---|
| Laravel session (~2h idle, rotates on its own) | **yes** |
| `cf_clearance` (IP-bound, rotates with carrier NAT) | **no** |

On a static IP this inverts and the two changes become complements: the box
fixes clearance, auto-login fixes the session, and the cookie treadmill ends.

### The test to run first, from the server

Cloudflare is adaptive and a **datacenter IP is far more likely to be
challenged than a residential or mobile one**. The login page that answered
cleanly from a home connection may well return "Just a moment…" from a VPS.

Run the probe above from the box **before** building auto-login. Testing
after deployment costs one request; building first and discovering the box
is challenged costs a day.

---

(## 5. Multiple Gemini accounts — already built

`GEMINI_API_KEYS=key_one,key_two,key_three` (comma separated). The single
`GEMINI_API_KEY` form still works untouched.

Quota is **per project**, so a second key is a second full allowance of the
*same* model. The fallback therefore tries **every account on the cheap model
before any account on a dearer one**, and the starting account rotates so one
is not drained while the others sit idle.

Measured impact against a real day (283,931 tokens, ~17 calls):

```
1 account   ~12 transcription standard passes/day
4 accounts  ~48
```

Keys go in the `0600` `.env`, **never** in the unit file — `/etc` is
world-readable. Exhaustion is tracked per `(account, model)` using an
8-character hash; no key ever reaches a log.

**Verified live 2026-09-01** with three accounts: successive calls started
on 904e2e6b → 2b82aa6d → 17f90b43 → 904e2e6b, all on the cheap model. The
full matrix is 3 accounts × 4 models = 12 attempts before giving up.

**A defect the third key exposed.** `generate_topic` in `warmpool.py` built
its own provider instead of using `build_provider()`. It read the
**singular** `GEMINI_API_KEY`, so an installation setting only
`GEMINI_API_KEYS` produced no titles at all, silently; and it passed a lone
key with no `keys=`, leaving a one-account pool where neither the rotation
nor the model chain could engage. Fixed, and the rotation now has tests —
it had none, which is how this survived.

**Anything that builds a provider must go through `build_provider()`.**
That is the only place that assembles the key pool, and bypassing it opts
silently out of every protection above.
)
**Worth checking yourself:** whether multiple free-tier projects is within
Google's terms. It works technically; the terms are not something this
project can verify for you. Moot if the accounts are paid.

---

## 6. Warm wide, because the pool empties fast

The unit now ships `--pool-size 24 --concurrency 2`, up from `3` and `1`.

Warming spends **no platform minutes**, so the only cost of warming a task
somebody else takes is CPU that was otherwise idle. Against a 44-task drop
that empties in four hours, a pool of 3 is permanently behind.

**Concurrency is a memory budget, not a speed dial:** roughly 750 MB per
model-bearing pipeline. 2 wants ~1.5 GB free and warms a task about every 50
seconds. Raise it only if the box has headroom.

### The cheap/expensive split — BUILT 2026-09-01

The two products do not cost the same to warm:

| | free (local CPU) | Gemini cost |
|---|---|---|
| Translation | ASR | translate: **2–3 calls** |
| Transcription | ASR + diarization | topic + standard pass: **5–14 calls, 14–60k tokens** |

Warming 40 translations is ~100 calls. Warming 40 transcriptions is ~320
calls **on work that is 23–75% likely to go to someone else**.

The intended shape is **wide and shallow, then narrow and deep**:

- **Stage A** — ASR and diarization on everything. Zero quota. A task with a
  transcript is already claimable and reviewable.
- **Stage B** — topic and standard pass on the shortest 10–12 only, refilled
  as tasks are taken or claimed.

**How it works now.** `warm_task(..., allow_ai=False)` runs Stage A only.
`run_cycle` takes the AI budget off the front of the shortest-first
candidate list — `EAGLE_WARM_AI_LIMIT`, default 12. The cycle log reports
the split:

```
[warm] ready=0 processed=4 asr_only=2 deferred=0 failed=0
```

**One case needed its own rule.** A translation whose transcript is already
banked cannot be advanced by Stage A at all — only the provider can produce
the French. Out of budget it is *deferred* rather than given a slot to
accomplish nothing. That is task 4705's exact state.

**Two things to watch on the box:**

- `deferred=` staying high means the budget is too low for the translation
  mix — translations need Stage B to be claimable at all, transcriptions
  do not.
- A transcription warmed outside the budget is claimable but has had **no
  standard pass**: raw ASR, contractions unexpanded, fillers intact. That is
  what a worker got before the standard pass existed, and `is_warm` counts
  it as ready. The interface does not currently distinguish the two. If
  review failures start clustering on long files, this is the first place
  to look.

**The default of 12 is a guess**, taken from the "4–5 tasks an hour" figure
rather than from measurement. `asr_only=` and `deferred=` are what will
correct it.

---

## 7. Still open, in priority order

1. **The claim race.** N workers racing one task still send N claim POSTs;
   only Eagle's 422 prevents a double claim. Needs a conditional-UPDATE
   reservation before the external call, plus a sweeper for reservations
   stranded by a crash between the reserve and the call.
2. **The four accounts share one password**, and it appears in a session
   transcript. Change before anyone else signs in.
3. **`max_concurrent_tasks` is a flat 3.** During a drop that is a claiming
   cap — banking three short tasks while they exist is an advantage. Raising
   it is safe for 5-minute tasks and dangerous for 20-minute ones, so it
   should key off duration rather than become a bigger flat number.
4. **Transcription review vocabulary is still unknown.** No transcription
   task has been reviewed yet, so its accepted/rejected words have never
   been observed — the awarded feed has only ever shown `IN_PROGRESS`.
   Unrecognised statuses are treated as pending and logged once:
   watch for `[SYNC] ... unrecognised status`.
5. **A warmed transcription outside the AI budget has no standard pass**,
   and nothing in the interface says so (section 6).
6. **No database/cache retention policy exists yet — deferred until there's
   meaningful production history to measure against.** Found during a
   storage audit (2026-09-05), not acted on at the time: deliberately
   deferred while
   stabilization continues. Nothing here has been changed.

   - `AISegmentCache` (`apps/transcriptions/models.py:748-787`) is a
     content-addressed dedup cache (key: operation, languages, model,
     `prompt_version`, sha256 of normalized text — no FK to any job/task)
     with **no TTL, no size limit, no scheduled or manual cleanup, no
     delete path anywhere in the codebase.** It can grow indefinitely.
     Measured at the time of this audit: ~10MB of a ~29MB database.
   - `prompt_version` is part of its unique key
     (`core/services/ai_assist/hashing.py:35-95`), and `translate` alone
     has been bumped v1→v5 across real defect fixes — every row written
     under an old version is now structurally unreachable by the current
     lookup and permanently dead weight. **This is the first safe cleanup
     candidate**: delete rows whose `prompt_version` doesn't match the
     current `PROMPT_VERSIONS` value for that `operation` — provably
     unreachable, not a guess. Current-version rows are a genuine
     storage/cache-effectiveness/API-cost tradeoff, not an obvious prune.
   - Before touching any of this, measure: row count; rows by operation ×
     prompt_version; current-vs-obsolete split; approximate storage of
     each; cache age distribution; DB growth over time.
   - Same audit, same disposition (measure later, not now):
     `TranscriptionJob`/transcript/translation/AI-revision data has no
     scheduled retention policy either; `AIUsageRecord` deliberately
     survives job deletion via `SET_NULL` (a financial ledger, correctly
     kept); Eagle `Task`/`TaskEvent` rows should stay as audit history;
     media cache is already age-cleaned (`warmpool.purge_expired_audio`,
     4 days terminal / ~1h lost-race) and measured at ~686MB with 227GB
     free (6% used) — not urgent; a `.part` interrupted-download file has
     no orphan sweep, a minor and likely small leak.
   - **The measurement above was actually run, 2026-09-10.** Real numbers,
     the retention decision it led to (no sweep — disk is not the
     constraint, Gemini quota is), the 30-day revisit threshold, and a
     genuine index-usage correction (a separate pass claimed two indexes
     were unused; real `pg_stat_user_indexes` data showed that was only
     true for one of them) are all in
     `docs/FUTURE_MAINTENANCE_AI_CACHE_RETENTION.md`. This item's own
     numbers above are the 2026-09-05 estimate that document supersedes.
7. **Prompt/quality-gate enrichment — deferred, reference material saved.**
   Found while comparing our prompts against a coach's manual (external)
   translation-audit and transcription prompts: the automated Gemini
   translate prompt (`core/services/ai_assist/prompts.py`) has no French
   typography rules at all, and the multi-pass analysis→audit→repair→
   re-audit quality gate is never invoked automatically during warming for
   either product (`_warm_translation` sets no `quality_profile`; the
   transcription standard pass's own profile deliberately omits the audit
   stage after a real false-positive-revert incident). Full comparison and
   the source prompts: `docs/COACH_PROMPTS_REFERENCE.md`. Same disposition
   as item 6 — measure/decide later, nothing changed now.

### Done since this file was written

- **Stage A/B warming split** — section 6.
- **Undelivered submissions.** Outcome reconciliation only ever acted on
  rows the awarded feed returned, so a submission that never landed sat at
  `submitted` for ever: "Awaiting review" on the tasker's screen, and
  counted as delivered volume when deciding awards. Ours listed five
  transcription tasks; Eagle's feed listed one. Now flagged failed once the
  deadline is past plus `EAGLE_UNDELIVERED_GRACE_HOURS` (default 6), and
  **only** when the feed was read successfully — absence from a listing we
  could not read means nothing.
- **Failed jobs are warmable again.** A task whose pipeline failed was warm
  to nobody and a candidate for nothing. The FFmpeg outage stranded 42 of 46
  open tasks this way; fixing FFmpeg alone would have left every one of them
  poisoned. Retries are ranked behind never-started work, since there is no
  attempt counter.
- **The coach's rubric**, including the number-style rule restored behind
  its anti-hallucination guard, and a vocalization rule that had its two
  branches swapped — edited verbatim was being told to record coughs and
  strict verbatim not to.

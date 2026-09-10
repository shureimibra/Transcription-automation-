# AI segment cache — retention decision and baseline

Written 10 September 2026, against real production data. This is the
"measure first" step `BEFORE_DEPLOYMENT.md` item 6 asked for, now actually
done — that item was written 5 September against a mid-storage-audit
estimate (~10 MB of ~29 MB DB); this document supersedes it with the real
numbers and the decision they led to.

---

## 1. The baseline, measured live

Against `AISegmentCache` in production, 10 September 2026:

| | |
|---|---|
| Total rows | 69,871 |
| Obsolete rows (`prompt_version` behind the current one) | **0** |
| Table size (incl. indexes) | 66 MB |
| Actual text payload (`source_text_snapshot` + `result_text`) | ~8.5 MB |
| Whole database size | 139 MB |
| Oldest row | 2026-09-04 14:06 |
| Newest row | 2026-09-09 15:14 |
| Rows created in the last 24h of that window | 7,755 (11% of the total) |

**Zero obsolete rows is itself informative, not just good news.** The
oldest row is only 5 days old, so every row in the table postdates every
historical `prompt_version` bump (`translate` has been bumped v1→v5 across
real defect fixes — see item 6's original text). The "delete provably
unreachable rows" cleanup the original item flagged as free and safe has
nothing to act on *yet*, because this cache is younger than the last time
that would have mattered. It will start finding real obsolete rows the
next time a prompt gets a version bump while old rows are still within the
retention window — worth re-running this measurement then, not just on a
calendar date.

**The overhead ratio is the real finding.** ~8.5 MB of actual text is
costing 66 MB on disk — roughly 7-to-1. That is row/index overhead, not
bloat in the content itself (see §3).

## 2. Decision: no retention sweep yet

**Not built. Deliberately, not by default.**

The reasoning: disk is not the constraint here. 139 MB total, with the
free space this project already has on the media cache volume (~227 GB
free, 6% used per item 6) strongly suggesting no near-term disk pressure
on this box either. The actual constraint is Gemini API quota and
per-minute rate limits (see `_warm_translation`'s own docstring and the
cost audit earlier in this project's history) — evicting a *valid,
still-reachable* cache row to reclaim a few tens of megabytes, only to pay
a real Gemini request the next time that exact segment text needs
reprocessing, is trading a resource with slack for one without.

**This is a judgment call, not a free win** — unlike the index drop in §3,
which has no downside. A time-based retention policy (e.g. "delete
current-version rows older than N days") remains the honest tool if this
ever needs revisiting, structured the same way
`warmpool.purge_expired_audio` already handles the media cache. It is not
built because there is not yet enough history to size `N` against real
reuse patterns, not because the idea is wrong.

### Revisit threshold

**Re-run the measurement in §4 once the cache has 30+ days of history**
(so roughly **5 October 2026** onward), or sooner if `AISegmentCache`
crosses **500 MB** or **25%** of total DB size, whichever comes first.
Thirty days is enough time to see whether a `prompt_version` bump actually
produces a real body of obsolete rows (the free cleanup), and to observe
how far back a legitimate cache hit ever actually reaches — which is the
number a sane retention window should be sized against, not guessed.

## 3. Index correction — done, not deferred

A separate pass claimed both `content_hash` and `operation`'s standalone
indexes (`db_index=True` on each, independent of the composite
`ai_seg_cache_lookup_idx`) were at 0 scans and safe to drop together. Real
`pg_stat_user_indexes` data (10 September 2026) showed that was only true
for one of the four indexes involved:

| Index | Scans | Size | Verdict |
|---|---|---|---|
| `content_hash` (plain btree) | **0** | 9.6 MB | Unused — **dropped**, migration `0021` |
| `content_hash` (`_like`, pattern-ops) | 6,348 | 9.6 MB | Actively used — left alone |
| `operation` (plain btree) | 363 | 608 KB | Used, traced partly to a stray untracked diagnostic script (`dump_cache.py`, not part of the application) — left alone until fully understood |
| `operation` (`_like`, pattern-ops) | 87 | 608 KB | Lightly used — left alone |
| `unique_ai_seg_cache_entry` | 31 | 14 MB | **Never a removal candidate regardless of scan count** — this enforces the cache's actual correctness guarantee (no duplicate entries), it is not an optional read optimization |
| `ai_seg_cache_lookup_idx` | 136,611 | 14 MB | The real workhorse, exactly as designed |

Only the genuinely-zero-scan index was dropped. `content_hash` no longer
carries `db_index=True` on the model (migration `0021`); `operation` keeps
its own `db_index=True` deliberately, pending identifying the remaining
363/87 scans' real source.

## 4. The measurement script

Same script used to produce §1 and to decide §2 — re-run this, don't
re-derive it, when the revisit threshold in §2 is reached.

```bash
sudo -u admin /opt/shurz/.venv/bin/python /opt/shurz/backend/manage.py shell -c "
from django.db.models import Count
from django.utils import timezone
from datetime import timedelta
from apps.transcriptions.models import AISegmentCache
from core.services.ai_assist.hashing import PROMPT_VERSIONS

total = AISegmentCache.objects.count()
print('=== total rows ===')
print(total)
print()

print('=== rows by operation x prompt_version (current base marked) ===')
rows = AISegmentCache.objects.values('operation', 'prompt_version').annotate(n=Count('id')).order_by('operation', '-n')
current_total = 0
obsolete_total = 0
for r in rows:
    base = PROMPT_VERSIONS.get(r['operation'], 'v1')
    is_current = r['prompt_version'] == base or r['prompt_version'].startswith(base + ':')
    tag = 'current' if is_current else 'OBSOLETE'
    if is_current: current_total += r['n']
    else: obsolete_total += r['n']
    print(f\"  {r['operation']:12} {r['prompt_version']:14} {r['n']:6}  [{tag}]\")
print()
print(f'current: {current_total}  |  obsolete (provably unreachable): {obsolete_total}')
print()

print('=== approximate storage (chars of source_text_snapshot + result_text), current vs obsolete ===')
current_chars = 0
obsolete_chars = 0
for row in AISegmentCache.objects.only('operation', 'prompt_version', 'source_text_snapshot', 'result_text').iterator():
    base = PROMPT_VERSIONS.get(row.operation, 'v1')
    is_current = row.prompt_version == base or row.prompt_version.startswith(base + ':')
    size = len(row.source_text_snapshot or '') + len(row.result_text or '')
    if is_current: current_chars += size
    else: obsolete_chars += size
print(f'current: ~{current_chars/1024:.0f} KB of text  |  obsolete: ~{obsolete_chars/1024:.0f} KB of text')
print()

print('=== cache age distribution ===')
now = timezone.now()
for label, since in [('last 24h', 1), ('last 7d', 7), ('last 30d', 30)]:
    print(f'  created within {label}:', AISegmentCache.objects.filter(created_at__gte=now - timedelta(days=since)).count())
oldest = AISegmentCache.objects.order_by('created_at').first()
newest = AISegmentCache.objects.order_by('-created_at').first()
print('  oldest row:', oldest.created_at if oldest else None)
print('  newest row:', newest.created_at if newest else None)
print()

print('=== whole-database size (baseline for future growth comparison) ===')
from django.db import connection
with connection.cursor() as cur:
    engine = connection.vendor
    print('DB engine:', engine)
    if engine == 'postgresql':
        cur.execute(\"SELECT pg_size_pretty(pg_database_size(current_database()))\")
        print('total DB size:', cur.fetchone()[0])
        cur.execute(\"SELECT pg_size_pretty(pg_total_relation_size('transcriptions_aisegmentcache'))\")
        print('AISegmentCache table size (incl. indexes):', cur.fetchone()[0])
    elif engine == 'sqlite':
        cur.execute('PRAGMA page_count')
        pages = cur.fetchone()[0]
        cur.execute('PRAGMA page_size')
        page_size = cur.fetchone()[0]
        print('total DB size:', f'{pages * page_size / 1024 / 1024:.1f} MB')
"
```

Also worth re-running at the same time — the index-scan check from §3, to
see whether `operation`'s standalone index has picked up more real (not
`dump_cache.py`) usage, or is still only explained by that script:

```bash
sudo -u admin /opt/shurz/.venv/bin/python /opt/shurz/backend/manage.py shell -c "
from django.db import connection
with connection.cursor() as cur:
    cur.execute(\"\"\"
        SELECT indexrelname, idx_scan, pg_size_pretty(pg_relation_size(indexrelid))
        FROM pg_stat_user_indexes
        WHERE relname = 'transcriptions_aisegmentcache'
        ORDER BY idx_scan ASC
    \"\"\")
    for row in cur.fetchall():
        print(row)
"
```

# Which branch is which

**The problem this solves.** The repo has several old, unrelated branches
lying around, and at a glance it looks like `deploy-fix` and `main` might
have diverged — like fixes landed on one and never reached the other. They
haven't. This is what a `git fetch --all` + comparison actually showed on
2026-09-17, and how to re-check it yourself if this ever looks wrong again.

---

## `deploy-fix` is the one that matters

**`deploy-fix` is the production branch.** It's what the server's `git pull`
tracks, and it contains everything.

Checked directly:

```bash
git merge-base deploy-fix origin/main
```

returned `e9d91cf...` — which is `origin/main`'s own tip at the time. That
means `origin/main` is not a separate line of work with commits deploy-fix
is missing; it's a strict **ancestor** of `deploy-fix`. Every commit on
`main` is already inside `deploy-fix`'s own history, plus 38 more on top of
it. Confirmed both directions:

```bash
git log --oneline deploy-fix..origin/main   # empty -- main has nothing extra
git log --oneline origin/main..deploy-fix   # everything deploy-fix is ahead by
```

If this command ever comes back non-empty in the first direction, *that*
would be the real problem worth chasing — it would mean something landed on
`main` (or was merged there) that never made it into `deploy-fix`.

## What actually caused the confusion

The **local** `main` branch (not `origin/main`) is a stale ref, last moved
long before this project's recent work. `git branch -vv` shows it as
`[origin/main: ahead 4, behind 47]` — genuinely far behind — but nothing
uses it. It is not what the server deploys from, and it is not what
`origin/main` points at. Don't read anything into it.

## The other branches are unrelated experiments

`1st-architecture`, `architecture-redesign`, `2architecture-redesign`,
`new-architecture-attempt`, and `origin/Instep` are old, abandoned
experiments, not part of the current production line. One of them
(`2architecture-redesign`) is even checked out in a **different repository
on disk** (`.../scratch/transcription_platform`, not this one), which is
why `git branch -a -vv` shows a worktree path next to it instead of a normal
tracking relationship. Ignore all of these for deploy purposes.

## The one command to re-run if this ever feels uncertain again

```bash
git fetch --all
git log --oneline deploy-fix..origin/main
```

Empty output means deploy-fix still has everything main has. That's the
whole check.

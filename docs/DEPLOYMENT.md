# Running the poller so it survives a reboot

**The problem this solves.** Until now the poller has only ever run inside a
terminal someone left open. Close the window, reboot, or let the machine
sleep, and the queue stops updating — it does not empty and it does not
error, it **freezes** at whatever it last saw. A worker can then claim a task
Eagle gave to somebody else hours ago.

That is exactly the "dashboard says 74, Eagle says 65" problem. The
mechanism that retires vanished tasks was always correct; nothing was
running it.

---

## Which one to install

| Where | Use | Solves |
|---|---|---|
| The development machine | `deploy/install-windows-task.ps1` | Surviving reboots |
| A cloud box with a static IP | `deploy/eagle-cycle.service` | Reboots **and** the session expiry |

The second one matters more than it looks. See *The IP problem* below.

---

## Windows, now

Run as Administrator from the repository root:

```
powershell -ExecutionPolicy Bypass -File deploy\install-windows-task.ps1
```

It registers a task that starts **at boot** (not at logon — the queue should
be current before anyone signs in), runs as SYSTEM, restarts every minute if
it dies, and never times out. Windows' default execution limit is three
days, after which it would kill the poller and say nothing.

Watch it:

```powershell
Get-Content "$env:PROGRAMDATA\transcription\eagle-cycle.log" -Tail 40 -Wait
Get-ScheduledTask EagleCycle | Get-ScheduledTaskInfo
```

Remove it:

```powershell
Unregister-ScheduledTask EagleCycle -Confirm:$false
```

## Linux, for production

```bash
sudo cp deploy/eagle-cycle.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eagle-cycle
```

Edit the paths and `User=` first. Secrets go in
`/srv/transcription/backend/.env` at `0600` — **never** in the unit file,
which lives in `/etc` and is world-readable.

```bash
systemctl status eagle-cycle
journalctl -u eagle-cycle -f
journalctl -u eagle-cycle | grep NETWORK      # public IP changes
```

---

## The IP problem — why the cloud box is not just about cost

Cloudflare's `cf_clearance` token is bound to **the IP address that earned
it**. On a fixed connection that is invisible. On a **mobile hotspot** it is
the whole story: carrier NAT reassigns the public address every few hours,
and every reassignment silently invalidates the token.

Measured on this project, two consecutive sessions died **3.3 hours** and
**2.8 hours** apart. That regularity never looked like a corrupted session;
it looks like a lease expiring.

The poller now records its address every cycle and says so when it moves:

```
[NETWORK] public IP changed 102.2.94.153 -> 102.2.88.7. Cloudflare
clearance is bound to the address that earned it, so any Eagle session
stored before now is probably dead through no fault of the cookie handling.
```

**Check that line before blaming anything else.** If the address moved at
the same moment the session died, no amount of cookie handling on our side
would have prevented it — only a browser can earn a new clearance token.

A box with a static IP removes the problem entirely: clearance stays valid
until Cloudflare's own expiry, and Laravel's ~2 hour idle timeout becomes
the only limit — which the 90-second poll already keeps at bay.

---

## What the settings mean

Both installers set the same two, and both are deliberate:

**`AI_MAX_REQUESTS_PER_MINUTE=15`** — under the provider's own per-minute
ceiling. Being refused costs a whole revision and leaves the task cold;
warming has no deadline that would justify the risk.

**`EAGLE_WARM_QUALITY_PROFILE=volume`** — full detection, no automatic
repair. About **three** provider requests per task instead of thirteen. On a
free-tier quota that is the difference between fitting the day's work and
stopping before lunch. Findings are reported for a human to act on.

`--pool-size 3 --concurrency 1` warms three tasks ahead, one at a time.
Raising concurrency is the fastest way back to rate-limit failures.

Everything else — the Gemini key, the database, the Fernet key, the Eagle
submitter name — belongs in `.env`. See `backend/.env.example`.

---

## Checking it is actually working

```bash
journalctl -u eagle-cycle | grep -E '\[sync|\[warm' | tail
```

A healthy cycle looks like:

```
[sync:transcription] seen=15 new=0 updated=15 taken=2 minutes=None
[sync:translation]   seen=49 new=1 updated=48 taken=3 minutes=15
[warm] ready=2 processed=1 failed=0
```

`taken=` is tasks that left Eagle's pool between cycles — other workers
claiming them. Seeing it move is the clearest sign the poller is real and
current.

**If `taken=` is always 0 and `seen=` never changes, the session is dead.**
Check the admin banner and the `[NETWORK]` lines.

---

## Two things it does not do

**It does not renew the Eagle session.** Nothing can, automatically: only a
browser can pass a Cloudflare challenge. When the session expires, an
administrator signs in and stores a fresh cookie. The poller keeps a *live*
session alive; it cannot resurrect a dead one.

**It does not claim or submit anything.** It only discovers, retires and
pre-processes. Claiming spends platform minutes and starts an irreversible
one-hour clock, so it stays a deliberate act by a person.

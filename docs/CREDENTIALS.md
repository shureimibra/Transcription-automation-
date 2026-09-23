# Every secret, where it lives, and what breaks without it

The operator's reference. One page, so that handing this machine to somebody
else — or picking it up yourself in six months — does not mean reading the
source to find out what a value does.

**Nothing in this file is a secret.** It names the settings and explains
them; the values live only in `backend/.env` on the server, which is
`chmod 600` and never committed.

---

## The rule that matters most

`DJANGO_SECRET_KEY` and `EAGLE_CREDENTIAL_KEY` are **not interchangeable and
must both be set in production.**

If `EAGLE_CREDENTIAL_KEY` is missing, the code derives an encryption key from
`DJANGO_SECRET_KEY` instead — convenient in development, dangerous in
production. It means **rotating the secret key silently orphans every stored
Eagle cookie**: they are still in the database, still encrypted, and no
longer decryptable. The symptom is every Eagle call failing at once with no
obvious cause.

Set both. Never rotate either casually.

---

## 1. Refuses to start without these

The server will not boot if any of these is missing or unsafe. That is
deliberate — a refusal you cannot miss beats a warning nobody reads.

| Setting | What it is | If it is wrong |
|---|---|---|
| `DJANGO_ENV` | `production` on the server, `development` on a laptop | Wrong value silently loosens every protection below |
| `DJANGO_SECRET_KEY` | 50+ random characters, signs sessions and tokens | Anyone holding it can forge a login as anybody |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated domains/IPs this server answers for | Accepts any Host header |

Generate the key once and keep it:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

---

## 2. The application needs these to function

| Setting | What it is | Where you get it |
|---|---|---|
| `DB_ENGINE` | `postgres` | Fixed value. Without it the app quietly uses SQLite and the row locking that prevents two workers taking one task becomes a no-op |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Database name, user, password | You choose them when creating the database |
| `POSTGRES_HOST` / `POSTGRES_PORT` | `localhost` / `5432` | Defaults are correct for a single box |
| `EAGLE_CREDENTIAL_KEY` | A Fernet key encrypting stored Eagle cookies | Generate once, see below |
| `GEMINI_API_KEYS` | Comma-separated Gemini keys | Google AI Studio. Singular `GEMINI_API_KEY` also works |
| `HF_TOKEN` | Hugging Face read token | Required by the speaker-diarization model |
| `EAGLE_USER_AGENT` | The exact browser string cookies were copied from | Cloudflare ties clearance to the user agent as well as the IP |
| `DJANGO_CORS_ORIGINS` | Where the front end is served from | Your own domain |

Generate the credential key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## 3. Not in the environment: the Eagle cookies

These are the most sensitive values in the system and they are **never** in
`.env`, never in a command line, and never in a log.

They live encrypted in the database, written only through:

```bash
python manage.py eagle_credential --set translation     # paste, then Ctrl-D
python manage.py eagle_credential --set transcription
```

The command reads from standard input, so the cookie does not land in your
shell history or in the process list where other accounts could read it.

To check without revealing anything:

```bash
python manage.py eagle_credential --check
```

**Workers never see these.** Media is fetched server-side and served through
our own endpoint; no cookie, header or Eagle URL reaches a tasker's browser.
That boundary is the reason the architecture looks the way it does.

---

## 4. Worth tuning, safe to ignore at first

Every one has a working default. Change them when you have a reason.

| Setting | Default | What it changes |
|---|---|---|
| `EAGLE_WARM_POOL_SIZE` | 12 | How many tasks to keep prepared, **per kind** — transcription and translation each pursue this many ready tasks independently, so combined ready tasks can run above this number when one kind has more supply than the other |
| `EAGLE_WARM_CONCURRENCY` | 2 | Pipelines at once. **A memory budget** — roughly 750 MB each |
| `EAGLE_WARM_AI_LIMIT` | 12 | How many tasks per cycle may spend Gemini quota |
| `EAGLE_AUDIO_RETENTION_DAYS` | 4 | How long cached audio is kept |
| `EAGLE_UNDELIVERED_GRACE_HOURS` | 6 | How long past its deadline before a missing submission is called undelivered |
| `THROTTLE_LOGIN` | 30/min | Sign-in attempts **per internet connection, not per person** |
| `THROTTLE_AI` / `THROTTLE_MEDIA` / `THROTTLE_SUBMIT` | 30/h, 120/h, 60/h | Limits on the actions that cost money |
| `AI_MAX_REQUESTS_PER_MINUTE` | 15 | Kept below the provider ceiling; being refused costs a whole revision |
| `DJANGO_HSTS_SECONDS` | 0 | **Leave at 0 until TLS is proven.** Turning it on tells browsers to refuse plain HTTP for that long and cannot be withdrawn early |

---

## 5. Accounts and passwords

You issue every account. There is no self-service signup and no password
reset email — this deployment has no mail service, and a reset link that
silently goes nowhere is worse than none.

From the admin page (works on a phone): **Accounts → Reset password**.

From a terminal:

```bash
python manage.py worker list                 # who exists, who can work, who holds tasks
python manage.py worker password <username>  # prompts twice, hidden
python manage.py worker disable <username>   # blocks login AND frees their queue
python manage.py worker enable <username>
python manage.py create_worker --code W-07   # a new account
```

**Disabling closes two locks**, the account and the worker profile, because
closing one and assuming they are out is how somebody signs in the next
morning. It also releases any tasks they were holding — those are unreachable
by anybody else otherwise, and the one-hour clock runs out on them.

---

## 6. If something is lost

| Lost | Consequence | Recovery |
|---|---|---|
| `DJANGO_SECRET_KEY` | Everyone signed out; **stored Eagle cookies orphaned if `EAGLE_CREDENTIAL_KEY` was never set** | Set a new key, re-paste both Eagle cookies |
| `EAGLE_CREDENTIAL_KEY` | Stored Eagle cookies cannot be decrypted | Set a new key, re-paste both cookies |
| Gemini keys | Warming stops producing translations; ASR still runs | Issue new keys, update `GEMINI_API_KEYS` |
| `HF_TOKEN` | Speaker diarization fails; transcription still works | New token from Hugging Face |
| Eagle cookies | Nothing syncs, warms or submits | Re-paste from a browser |
| A tasker's password | They cannot sign in | Reset it; nothing else is affected |
| The database | Everything | See backups below — **there are none yet** |

---

## 7. What is not done yet

Honest gaps, so they are not discovered at the wrong moment.

- **No backups.** Nothing backs up PostgreSQL. Before real work runs through
  this, a nightly `pg_dump` off the box is the minimum.
- **No secret rotation procedure.** Changing `DJANGO_SECRET_KEY` safely means
  setting `EAGLE_CREDENTIAL_KEY` first, or re-pasting the cookies after.
- **Seed and mock data.** The development database still holds test tasks and
  a `toolonly` test account. A fresh production database avoids inheriting
  them; a copied one does not.

---

## 8. Never do these

- Never put a password or cookie on a command line — it lands in shell
  history and in the process list, readable by any other account on the box.
- Never commit `.env`. It is gitignored; keep it that way.
- Never give a worker an Eagle cookie, session, or URL. The whole design
  exists so they never need one.
- Never enable HSTS before HTTPS is proven working.

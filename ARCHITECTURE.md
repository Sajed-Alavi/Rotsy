# Rotsy Architecture

How the backend is put together and, more usefully, **why** — the constraints
that make it this shape rather than another. [`AGENTS.md`](./AGENTS.md) holds
the load-bearing invariants; this document explains the structure they live in.

`backend/notifications/` sits outside this stack entirely — it depends on the
app through one file (`ports.py`) and the app depends on it only to call
`setup()`. See its own section below.

Rotsy is a **modular monolith with background workers**. One deployable, clear
internal boundaries. Not microservices: the traffic does not warrant them, and
splitting would trade a solved problem (a function call) for an unsolved one
(a network hop that can fail).

---

## Layers and dependency direction

```
   routers/            HTTP only: parse, authorize, delegate. No business logic.
        │
        ▼
   services/           Business logic and orchestration. Framework-agnostic.
   modules/            One adapter per external system.
        │
        ▼
   core/               Infrastructure + contracts: jobs, cache, security,
   models/             access control, policy, events, provider Protocols.
```

**Dependencies point downward, never up and never sideways between modules.**
`modules/github` does not import `modules/gitlab`; neither imports a router.

This is enforced by review, not tooling, and it did drift: the Telegram
dispatcher imported two router functions because that was where the
orchestration lived, which forced function-scope imports to dodge a circular
import, and `services/scan_report_pdf.py` reached into `routers/scan/` for a
sort helper. Both are fixed — the orchestration moved to
[`services/analysis.py`](backend/app/services/analysis.py) and
[`services/project_repositories.py`](backend/app/services/project_repositories.py),
the sort rules to [`core/finding_order.py`](backend/app/core/finding_order.py).

Checking for regressions:

```bash
grep -rn "from \.\.routers\|from \.\.\.routers" backend/app/services backend/app/modules backend/app/core
```

Anything it finds is a layering violation.

---

## Authorization

Two independent axes, and most actions need both:

| Axis | Question | Where |
| ---- | -------- | ----- |
| Permission key | *What* may this user do, anywhere? | Roles → `RequirePermission` |
| Project membership | *Which* Projects may they do it to? | `project_members` → `core/project_access.py` |
| Repository/image rules | *Which* repos and images? | `core/access_control.py` |

Closed by default on every axis: no membership row means no access to a
Project, whatever global permissions the user holds. The seeded `admin` role
bypasses membership — an administrator locked out of a Project would have no
way back in through the app.

**Decisions live in [`core/policy.py`](backend/app/core/policy.py)**, one
named function per action, callable from a router, a service, or the Telegram
bot. Expressing the pair as two FastAPI dependencies worked only when the
Project id was a path parameter and only while every new route remembered both
halves — and neither held. `POST /modules/sonar/repositories/{id}/run-analysis`
takes its Project from a row, could not use `require_project_access`, and
shipped gated on the global permission alone; any holder of `projects:write`
could trigger analysis on a Project they were not a member of. The bot,
needing the same rules without a dependency chain, re-implemented them by hand.

That bug passed the whole test suite, because every test called services
directly and none went through the endpoint. Hence:

**`tests/test_api_authorization.py` drives the real app over ASGI.** A route
whose `dependencies=[...]` is missing a check is invisible to a service-level
test and obvious to this one.

---

## Jobs

Anything slow is a job: `core/jobs.py`, Redis-backed, `pending → running →
done/failed/cancelled`, progress streamed over SSE. The API enqueues and
returns a job id; it never does the work in the request.

Cancellation is real, not advisory — the runner holds the `asyncio.Task` and
cancels it, and every layer that shells out kills its child process before
re-raising. A cancel that leaves a subprocess running is worse than no cancel,
because the UI then lies about it.

Each running job sets a correlation id (`job-<id>`), so every log line it
causes — including lines from libraries like `httpx` — carries it.

---

## Events

`core/events.py` is a small in-process publish/subscribe bus.

Jobs used to call notification code directly: the analysis worker imported
Telegram, the backup handler imported it again, the scanner-database handler a
third time, each with its own `try/except` so a chat failure could not fail the
job. That is N producers wired to M channels by hand — adding email would mean
editing every one again.

Now a job states what happened:

```python
await events.publish(events.AnalysisCompleted(project_id=…, quality_gate=…, …))
```

and stops caring who listens. `publish` never raises and never lets a
subscriber's failure reach the publisher, so emitting an event is as safe as
the `try/except` it replaced.

In-process on purpose. Subscribers enqueue work or send a message; none need
durability beyond the job that triggered them, and an event lost to a process
death is an event whose job also died. A broker would add an operational
dependency to buy a guarantee nothing currently needs.

---

## Notifications — a self-contained app

[`backend/notifications/`](backend/notifications/) owns *telling somebody
something happened*, end to end. It sits beside `app/` rather than inside it,
so the boundary is visible in the directory listing and not only in imports.

```
notifications/
├── message.py      Notification, Audience, Attachment, Severity — plain data
├── service.py      dispatch across every configured channel
├── renderers.py    produce attachment bytes, lazily, without knowing how
├── channels/       one module per mechanism, behind a Protocol
│   ├── base.py     NotificationChannel
│   └── telegram.py
├── subscribers.py  which events become notifications, and the wording
└── ports.py        the ONLY file that imports from `app`
```

Four properties make it an application rather than a helper module:

**1. Producers do not depend on it.** A job publishes an event. This package
subscribes. Adding a channel, rewording a message, or removing a notification
never touches a job handler — and a worker cannot accidentally acquire a
dependency on Telegram, which is exactly what had happened.

**2. Messages carry no channel markup.** The analysis worker used to build
strings containing `<b>…</b>` and escape its own interpolations, because
Telegram renders HTML. A worker should not know that. It states a title,
fields and a severity; each channel renders that in whatever it speaks. All
escaping now lives in the Telegram channel, the one place that knows escaping
is needed.

**3. Attachments are callables, not bytes.** An analysis report is an
unbounded query over every issue and hotspot plus a multi-page render.
`Attachment.load` is invoked only after a configured channel has resolved a
real recipient, so a deployment with no channel configured — the common case —
never builds one.

Usable directly by anything, not only by events:

```python
from app.notifications import Audience, Notification, Severity, dispatch

await dispatch(Notification(
    title="Retention sweep removed 412 tags",
    audience=Audience.administrators(),
    severity=Severity.INFO,
))
```

`dispatch` never raises and returns how many recipients were reached.

**4. One file touches the host.** `ports.py` names everything the package
needs from the rest of the backend — the event bus, a session factory,
identity/access lookups, the Telegram client — and nothing else in the folder
imports `app`. Enforceable:

```bash
grep -rn "from app\|import app" backend/notifications --include=*.py | grep -v ports.py
```

That should print nothing. Moving the package elsewhere means rewriting that
one file. Two choices exist to keep the move cheap: `Attachment` is a
serialisable *description* (a `kind` plus `params`, resolved through
`renderers.py`) rather than a closure, so a notification can be rebuilt from a
JSON message on the far side of a queue; and `subscribers.register()` is the
single point tying it to an in-process bus.

It runs in-process today, which is right at this size — no extra container, no
transport to operate, and `dispatch` never raises so a failure stays
contained.

Adding a channel: implement `NotificationChannel` (`is_configured`,
`deliver`), register it in `notifications.setup()`. Nothing else changes.

---

## Provider contracts

Adapters sit behind Protocols in `core/`, so business logic depends on the
contract and never on a vendor.

| Protocol | Implemented by | Status |
| -------- | -------------- | ------ |
| `SourceProvider` (`core/source_provider.py`) | GitHub, GitLab | in use |
| `NotificationChannel` (`notifications/channels/base.py`) | Telegram | in use |
| `ImageRegistry` (`core/image_registry.py`) | Nexus | **contract only** |

`ImageRegistry` is defined but not yet adopted: `modules/nexus/registry.py`
still *is* the registry layer, and callers import it by name. That is the gap
between what Rotsy claims — a console for container and code security — and
what it supports. Defining the shape in a layer that may be depended upon
makes adding Harbor/GHCR/ECR additive work rather than a refactor of
everything that touches images. Adopting it is deliberately not done here: the
right time is when the second backend exists to check the contract against.

---

## Observability

Three health endpoints, three jobs:

| Endpoint | Auth | Answers |
| -------- | ---- | ------- |
| `/api/health/live` | none | Is the process serving? Touches nothing else. |
| `/api/health/ready` | none | Can it serve real traffic? 503 if Postgres is down. |
| `/api/health` | required | Detailed Nexus/Redis view for the Dashboard. |

Liveness deliberately checks no dependency. A liveness probe that fails
because Postgres blipped tells the orchestrator to restart a backend that was
working, turning a recoverable outage into a restart loop. Readiness takes an
instance out of rotation instead; Redis is *reported* but not required, since
losing it degrades the app rather than disqualifying it.

Before the split there was no unauthenticated probe at all, which is why
Postgres and Redis each had a compose healthcheck and the backend had none.
It has one now.

**Correlation ids** (`core/correlation.py`): every request gets one — inbound
`X-Request-ID` honoured, echoed on the response — and every job sets
`job-<id>`. A `contextvars.ContextVar` carries it across `await`, and a
logging filter attaches it to every record, so third-party lines are tagged
too. Not distributed tracing: one process, and this answers the question that
actually gets asked without a collector to operate.

---

## Reliability and abuse control

**Retries are opt-in per job type** (`core/jobs.JobPolicy`), and default to
off. Repeating work is only safe when the work is idempotent: re-running a
database download costs bandwidth, re-running an archive job produces a second
archive. So a type opts in, and the opt-in is where somebody has to think
about it. Today `scanner_db_update` (2 retries) and `collect_metrics`
(1 retry, 15-minute timeout) do; backups and analysis deliberately do not.

Timeouts are opt-in for the same reason — the scanner database download is
legitimately allowed 45 minutes, so a blanket default would turn a working
feature into a mystery failure on a slow link.

Crash recovery already existed: `JobQueue.reap_stranded` fails jobs left
mid-flight by a process that died holding them.

**Webhook idempotency** is claimed with Redis `SET NX` — one atomic round
trip. It was previously a read followed by a write, so a retry arriving
alongside the original (exactly when duplicates happen) could have both
deliveries read "not seen" and both enqueue the same analysis.

**Rate limiting** (`core/rate_limit.py`) had no equivalent anywhere before.
It matters most on login: passwords are bcrypt-hashed, so each attempt is
deliberately expensive *for the server*, which makes an unthrottled login both
a guessing oracle and a cheap way to burn CPU. Two buckets — per source
address and per username, because a distributed attempt on one account slips
under a per-IP limit entirely. Analysis triggering is bounded too, since each
run clones a repository.

The limiter **fails open**: if Redis is unavailable it allows the request. A
limiter that denies everything when its own store hiccups converts a cache
outage into a total one, and locks out the operator who would fix it.

---

## Data flow

```
  Browser
     │  HTTPS / SSE
     ▼
  routers/            authorize (core/policy.py) ─┐
     │                                            │ same decisions
     ▼                                            │
  services/           orchestration ◄─────────────┴─ modules/telegram (the bot)
     │
     ├──────────────► core/jobs (Redis)
     │                     │
     │                     ▼
     │                 workers/           execute
     │                     │
     │                     ▼
     │                 core/events        publish a fact
     │                     │
     │                     ▼
     │                 notifications/     subscribe, render, deliver
     ▼                     │
  models/ (Postgres)       ▼
                       channels/ ──► Telegram / …

  modules/ ──► Nexus · GitHub · GitLab · SonarQube · Trivy · Grype
```

---

## Testing

| Level | Where | Catches |
| ----- | ----- | ------- |
| Unit | most of `tests/` | logic in isolation |
| **HTTP** | `test_api_authorization.py` | routes missing a permission dependency |
| App | `test_notifications.py`, `test_observability.py` | wiring, contracts, event delivery |

```bash
docker compose --profile test run --rm backend-test pytest
```

The `api` fixture builds the real application and drives it with httpx's
ASGITransport, which does not run the lifespan — so no background loop starts
during tests, and `app.state` stays empty. An endpoint needing the job queue
therefore answers 503, which is *useful*: a 503 proves the request got past
every authorization dependency, which is what those tests assert.

---

## Deliberately not done

- **CI/CD pipeline generation, build runners, artifact storage.** A separate
  product, not a refactor. Building foundations for something undesigned tends
  to produce the wrong foundations, at full maintenance cost. The security
  constraint is worth writing down now though: build runners execute untrusted
  repository code and must never share a trust boundary with the API.
- **Directory restructure** into `platform/`/`domains/`/`adapters/`. The real
  problem was dependency *direction*, which is fixed without moving files.
  Renaming would produce an unreviewable diff and destroy `git blame` for no
  user-visible gain.
- **A message broker, Celery, microservices.** Nothing here needs them.

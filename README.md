# web-load-balancer

FastAPI gateway and **load balancer** for the distributed ray tracer.

The gateway is the only entry point for clients. It authenticates users, validates and limits render requests, stores task history in PostgreSQL, and distributes render tasks across two or more C# render servers ([web-ray-worker](../web-ray-worker)) using a **least-loaded** strategy.

> Part of the [raytracer](../) project. The other repos are [web-client](../web-client) (React UI) and [web-ray-worker](../web-ray-worker) (ASP.NET Core ray tracer).

---

## Architecture

```
Browser (React) ──HTTP + WebSocket──▶ web-load-balancer (FastAPI) ──▶ PostgreSQL
                                          │
                                          │  least-loaded selection
                              ┌───────────┼───────────┐
                              ▼           ▼           ▼
                         renderer-1   renderer-2   renderer-N (autoscaled)
```

- The browser only ever talks to the gateway.
- Only the gateway talks to the database. Renderers are stateless and can be restarted at any time.
- The gateway and renderers communicate over plain HTTP/JSON.

## Responsibilities

| Area | What the gateway does |
|---|---|
| Auth | Registration and login, issuing JWT tokens. HTTPS is in the backlog (see below). |
| Validation and limits | Rejects renders above the complexity cap and limits active tasks per user. |
| Load balancing | Health-checks renderers and sends each task to the least-loaded one. |
| Queue | Holds tasks when every renderer is full and estimates the wait time. |
| Progress | Polls renderers for progress and pushes it to clients over WebSocket. |
| History | Persists tasks, parameters, status, timings, the renderer used and the result image. |
| Admin | Shows renderers, their load, running and queued tasks, and all users' tasks. |
| Autoscaling | Starts and stops renderer containers via the Docker API, within min/max bounds. |

---

## Load balancing

### Renderer registry and health checks

The gateway keeps a registry of renderers, configured via `RENDERERS` or registered by the autoscaler. A background loop calls `GET /health` on every renderer every `HEALTH_INTERVAL` seconds:

```json
{ "name": "renderer-1", "running": 1, "capacity": 2 }
```

- A renderer that fails `HEALTH_FAILS` checks in a row is marked **down** and receives no new tasks.
- A down renderer that answers again is marked **up** automatically.
- If a renderer goes down mid-task, its tasks are marked `failed` (or re-queued; see the roadmap).

### Selection: least-loaded

For every new task:

1. Take all renderers that are **up** and have `running < capacity`.
2. Pick the one with the lowest load `running / capacity`.
3. On a tie, pick the one that has been idle the longest (or the first one alphabetically).
4. If none is available, put the task in the **queue**.

Round robin is intentionally **not** used, because render tasks vary a lot in cost and round robin ignores that.

The gateway also tracks its own count of tasks dispatched to each renderer. A task assigned between two health checks therefore counts immediately, so a burst of requests can't all land on the same server.

### Queue and wait estimate

When every renderer is full, the task gets status `queued` (FIFO). The client sees its position and an estimated wait:

```
estimated_wait ≈ (position_in_queue / total_capacity) × avg_render_time
```

Here `avg_render_time` is a moving average of recently finished tasks, ideally normalised by complexity (pixels × samples). Whenever a renderer frees a slot, the head of the queue is dispatched.

### Autoscaling

When `AUTOSCALE=true`, a background loop in `app/autoscaler.py` checks the queue and the load every `AUTOSCALE_INTERVAL` seconds and starts or stops renderer containers through the Docker API (the gateway container needs `/var/run/docker.sock` mounted):

- **Scale up** when tasks are queued or the load of the up renderers is at least `AUTOSCALE_UP_LOAD`, one container at a time, waiting until the new one answers `/health`.
- **Scale down** when the queue is empty and an autoscaled renderer has been idle for `AUTOSCALE_IDLE_SECONDS`, but only if the remaining renderers stay below `AUTOSCALE_UP_LOAD` without it.
- The renderer count always stays within `AUTOSCALE_MIN` / `AUTOSCALE_MAX`. Static renderers from `RENDERERS` count toward it but are never stopped.
- New containers use the `AUTOSCALE_IMAGE` image, join the gateway's own Docker network and are named `ray-worker-3`, `ray-worker-4`, and so on.
- A container that doesn't answer for `AUTOSCALE_START_TIMEOUT` seconds is removed. On shutdown the gateway removes the containers it started, and after a crash it adopts the ones still running.

---

## Limits

| Limit | Default | Env var |
|---|---|---|
| Max width × height | 1920 × 1080 | `MAX_PIXELS` |
| Max samples per pixel | 256 | `MAX_SPP` |
| Max complexity (pixels × spp) | 150,000,000 | `MAX_COMPLEXITY` |
| Max render time | 120 s, after which the task is cancelled | `MAX_RENDER_SECONDS` |
| Active tasks per user | 3 | `MAX_ACTIVE_PER_USER` |
| Stored tasks per user | 50 | `MAX_HISTORY_PER_USER` |

Requests over a limit get `422` with an explanation. The frontend validates the same rules, but the gateway is the source of truth.

---

## Public API (used by the frontend)

All endpoints except auth require `Authorization: Bearer <jwt>`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/register` | Create an account |
| `POST` | `/auth/login` | Get a JWT |
| `GET` | `/scenes` | List preset scenes |
| `POST` | `/renders` | Start a render: `{ scene, width, height, spp }` |
| `GET` | `/renders` | The user's task history |
| `GET` | `/renders/{id}` | Status, progress %, queue position, ETA, renderer |
| `GET` | `/renders/{id}/image` | Result PNG (partial while rendering) |
| `DELETE` | `/renders/{id}` | Cancel a queued or running task |
| `WS` | `/ws/renders/{id}` | Live progress updates |
| `GET` | `/admin/renderers` | *(admin)* Renderers, load and status |
| `GET` | `/admin/tasks` | *(admin)* All running and queued tasks |

Task statuses: `queued → running → done | cancelled | failed`.

## Renderer contract (what the gateway expects)

Each renderer implements:

| Method | Path | Description |
|---|---|---|
| `POST` | `/tasks` | Start a render. Returns `{ id }`, or `503` if full |
| `GET` | `/tasks/{id}` | `{ status, progress, error? }` |
| `GET` | `/tasks/{id}/image` | PNG, partial or final |
| `DELETE` | `/tasks/{id}` | Cancel |
| `GET` | `/health` | `{ name, running, capacity }` |

Keep this table in sync with the web-ray-worker README.

---

## Data model (PostgreSQL, `web-db` service)

- **users**: `id, username, password_hash, is_admin, created_at`
- **tasks**: `id, user_id, scene, width, height, spp, status, progress, renderer, renderer_task_id, queued_at, started_at, finished_at, error, image_path`

## Configuration

| Env var | Example | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://app:app@web-db:5432/web_db` | Database connection |
| `RENDERERS` | `http://renderer-1:8080,http://renderer-2:8080` | Static renderer list |
| `HEALTH_INTERVAL` | `2` | Seconds between health checks |
| `HEALTH_FAILS` | `3` | Failed checks before a renderer is marked down |
| `JWT_SECRET` | *(set in .env)* | Token signing key |
| `AUTOSCALE` | `true` | Turn the autoscaler on (off by default for local development) |
| `AUTOSCALE_MIN` / `AUTOSCALE_MAX` | `2` / `5` | Renderer count bounds |
| `AUTOSCALE_UP_LOAD` | `0.8` | Load at which a renderer is added |
| `AUTOSCALE_IDLE_SECONDS` | `60` | Idle time before an autoscaled renderer is stopped |
| `AUTOSCALE_IMAGE` | `web-ray-worker` | Image for new renderer containers |

## Tech stack

FastAPI · Uvicorn · httpx (async calls to renderers) · SQLAlchemy + asyncpg · Alembic · python-jose / passlib (JWT, password hashing) · Docker SDK for Python (autoscaling)

## Project structure

```
web-load-balancer/
├── app/
│   ├── main.py            # FastAPI app, startup of background loops
│   ├── config.py          # settings from env vars
│   ├── auth.py            # JWT, password hashing, dependencies
│   ├── db.py, models.py   # SQLAlchemy setup and tables
│   ├── balancer.py        # renderer registry, health checks, least-loaded pick
│   ├── queue.py           # FIFO queue, dispatcher, wait estimate
│   ├── autoscaler.py      # Docker-based scaling
│   └── routers/           # auth, renders, admin, websocket
├── tests/
├── Dockerfile
└── requirements.txt
```

## Running

**With the whole system** (from the main `raytracer` repo):

```bash
docker compose up --build
```

**Locally for development:**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export RENDERERS=http://localhost:5001,http://localhost:5002
uvicorn app.main:app --reload --port 8000
# HTTPS (backlog):
uvicorn app.main:app --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

API docs: `http://localhost:8000/docs`

## Known limitations

- **Single point of failure:** there is one gateway instance. If it stops, the system is unavailable even though renderers keep running. A production setup would run several gateways behind a failover mechanism with a shared database.
- An in-memory queue is lost if the gateway restarts. Queued tasks can be restored from the DB `queued` status on startup.

## Roadmap

- [ ] Renderer registry and health-check loop
- [ ] Least-loaded dispatch and proxying of status and image
- [ ] PostgreSQL models and task history
- [ ] Complexity and per-user limits
- [ ] JWT auth
- [ ] WebSocket progress
- [ ] Queue with wait estimate
- [ ] Admin endpoints
- [ ] Autoscaler
- [ ] Re-queue tasks from a renderer that went down

## Backlog

- [ ] HTTPS: run uvicorn with a TLS certificate (`--ssl-keyfile` / `--ssl-certfile`), self-signed for local use (bonus points per the assignment)

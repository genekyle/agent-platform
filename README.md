# Agent Platform Dev Startup

## Repo layout

- `apps/controlplane-api`: FastAPI control plane and training APIs
- `apps/mcp-mock`: capture server and observer pipeline
- `apps/controlplane-ui`: Vite/React frontend
- `infra`: local Postgres and Redis via Docker Compose
- `scripts`: dev startup, shutdown, and health-check helpers

## One command

From the repo root:

```bash
make dev
```

On first run, `make dev` will also:

- create a repo-local Python virtual environment at `.venv`
- install the Python service dependencies into that `.venv`
- install UI dependencies with `npm ci` if `node_modules` is missing

Then it starts:

- Postgres via Docker on `localhost:5433`
- Redis via Docker on `localhost:6379`
- Control Plane API on `http://localhost:8081`
- Capture Server on `http://localhost:8082`
- Controlplane UI on `http://localhost:5173`
- Training Chrome is started on demand per training session from the UI

## Stop everything

```bash
make dev-stop
```

## Health check

```bash
make doctor
```

## First-time setup

One command:

```bash
make setup
```

That prepares:

- `.venv` with both Python service requirement sets installed
- `apps/controlplane-ui/node_modules` via `npm ci`

If you only want the Python environment:

```bash
make python-setup
```

The virtual environment lives at `.venv`, and the dev scripts call `.venv/bin/python` directly, so you do not need to manually activate it for `make dev`.

## Repo hygiene

The repo root `.gitignore` is the canonical ignore policy for the monorepo.

Generated local state should stay out of version control, including:

- virtual environments
- `node_modules` and UI build output
- Python cache directories and pytest cache
- local dev logs and PID files
- `apps/mcp-mock/output/` observer artifacts
- local `.env` files

## What you were previously having to remember

Without the wrapper, local startup was effectively:

```bash
cd infra && docker compose up -d
cd apps/controlplane-api && python3 -m uvicorn main:app --reload --port 8081
cd apps/mcp-mock && python3 -m uvicorn app.main_server:app --reload --port 8082
cd apps/controlplane-ui && npm run dev
```

Then, in the UI:

1. Open `Training`
2. Create a training session
3. Start `Session Chrome`
4. Capture against that session-scoped browser

## Better packaging direction

This `make dev` wrapper is the low-friction fix. The next cleaner step would be to package the Python services and UI under a single process manager:

- `docker compose` for Postgres and Redis only, which you already have
- `overmind`, `foreman`, or `honcho` with a `Procfile` for the UI and both APIs
- session-scoped Chrome management stays in the API/UI instead of a global dev bootstrap

If you want, I can do that next and replace the shell launcher with a `Procfile.dev` setup.

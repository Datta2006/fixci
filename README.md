# FixCI

FixCI is a GitHub App that watches for **failed GitHub Actions runs**, diagnoses
the root cause with an LLM, generates a patch, **verifies the patch in an
isolated Docker sandbox**, and only then opens a fix pull request with the
evidence attached.

The core promise: **FixCI never pushes an unverified patch.** If it cannot apply
and re-run the failing command successfully within its retry budget, it posts a
diagnosis-only comment and pushes nothing.

## How it works

```
workflow_run (completed, failure)
        │
        ▼
  webhook.py  ── verify HMAC, filter, dedupe, ignore fixci/ branches
        ▼
github_client.py ── fetch failed job logs, commit diff, workflow YAML
        ▼
  log_analyzer.py ── trim + redact + classify (dependency | lint | test_failure | unsupported)
        ▼
      llm.py ── Claude → structured Diagnosis {root_cause, explanation, confidence, files_changed, patch}
        ▼
   patcher.py ── static validation (paths, size, secrets) + `git apply --check`
        ▼
    sandbox.py ── clone @ commit, apply patch, re-run ONLY the failing command
        ▼                         (Docker: python:3.12-slim, 1g mem, 180s timeout)
   ┌────┴─────────────────────────┐
   │ passed                       │ failed
   ▼                              ▼
 new branch fixci/fix-<id>   feed error back to the LLM (≤ MAX_ATTEMPTS)
 + PR with verification       → exhausted: diagnosis-only comment
   evidence
```

Every run is stored in SQLite (`db.py`) and exposed at `GET /runs`.

## Scope (MVP)

FixCI is deliberately narrow:

- **Python projects using pytest only.**
- Supported failure classes: **dependency/version pins**, **lint (ruff/black/flake8)**,
  and **simple test failures** from an obvious code change.
- Anything else is classified `unsupported` and gets a **diagnosis-only comment**.

## Stack

Python 3.12 · FastAPI + uvicorn · PyGithub (App JWT → installation token) ·
Anthropic Python SDK · Pydantic v2 · Docker SDK for Python · SQLite (stdlib
`sqlite3`) · FastAPI `BackgroundTasks` · python-dotenv · pytest · httpx.

## Project layout

```
app/
  main.py            FastAPI app, webhook endpoint, health check, /runs
  config.py          env settings (pydantic-settings)
  github_client.py   auth, fetch logs/diff/workflow, create branch/commit/PR/comment
  webhook.py         HMAC SHA256 signature verification + event filtering/parsing
  log_analyzer.py    strip ANSI/timestamps, trim, extract failing command, classify
  llm.py             prompt building, Claude call, JSON parsing/validation
  models.py          Pydantic models (FailureContext, Diagnosis, VerificationResult, …)
  sandbox.py         Docker runner: clone, apply patch, run command, enforce limits
  patcher.py         validate + apply unified diffs (git apply --check first)
  orchestrator.py    pipeline + retry loop + PR/comment content
  db.py              SQLite storage for run history
tests/               unit tests (analyzer, patcher, llm, webhook, api, orchestrator, db, sandbox)
demo_repos/          3 deliberately-broken sample repos + instructions
docker-compose.yml   local stack
Dockerfile
.env.example
```

## GitHub App setup

1. **Create the App**: GitHub → *Settings → Developer settings → GitHub Apps →
   New GitHub App*.
   - **Homepage URL**: anything (e.g. `http://localhost:8000`).
   - **Webhook URL**: your public URL + `/webhook` (see *Exposing the webhook*).
   - **Webhook secret**: a long random string — this becomes `GITHUB_WEBHOOK_SECRET`.
2. **Repository permissions** (exactly these):
   - **Actions**: Read
   - **Contents**: Read & write
   - **Pull requests**: Read & write
   - **Metadata**: Read-only (mandatory)
3. **Subscribe to events**: check **Workflow run** only.
4. **Generate a private key** and save the `.pem`; set `GITHUB_PRIVATE_KEY_PATH`
   to its path (mounted read-only in compose).
5. Note the **App ID** → `GITHUB_APP_ID`.
6. **Install the App** on a repository (or all repos).

## Exposing the webhook locally

GitHub needs a public HTTPS endpoint. Pick one:

**ngrok**

```bash
ngrok http 8000
# use https://<subdomain>.ngrok-free.app/webhook as the App's Webhook URL
```

**smee.io** (no account)

```bash
npx smee-client --url https://smee.io/<your-channel> --target http://localhost:8000/webhook
# set the App's Webhook URL to https://smee.io/<your-channel>
```

## Running with docker-compose

```bash
cp .env.example .env          # fill in GITHUB_APP_ID, secret, ANTHROPIC_API_KEY…
# put the App private key next to compose as private-key.pem
docker compose up --build
curl http://localhost:8000/health   # {"status":"ok"}
```

The compose file mounts the Docker socket so the service can spawn sandbox
containers, and a named volume for the SQLite database.

> **Security note:** mounting `/var/run/docker.sock` grants the container
> control of the Docker daemon. That is a *local development* convenience. For
> production, run FixCI on a dedicated host and front the socket with a
> restricted proxy.

### Running without Docker (bare metal)

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # edit values
uvicorn app.main:app --reload --port 8000
```

## Configuration

| Variable | Meaning | Default |
| --- | --- | --- |
| `GITHUB_APP_ID` | GitHub App ID | – |
| `GITHUB_PRIVATE_KEY_PATH` | Path to the App private key PEM | `private-key.pem` |
| `GITHUB_WEBHOOK_SECRET` | Webhook HMAC secret | – |
| `ANTHROPIC_API_KEY` | Anthropic API key | – |
| `ANTHROPIC_MODEL` | Model name | `claude-sonnet-4-5` |
| `MAX_ATTEMPTS` | Diagnosis/verify retries | `3` |
| `SANDBOX_TIMEOUT` | Seconds per verification command | `180` |
| `DATABASE_URL` | SQLite URL (`sqlite:///path`) | `sqlite:///fixci.db` |
| `SANDBOX_ENABLED` | Set `false` to force diagnosis-only mode | `true` |

## Tests

```bash
pip install -r requirements.txt
pytest -q
```

Covers webhook signature verification, log trimming/classification, patch
validation, LLM JSON parsing, the orchestrator's safety guarantees, the SQLite
store, and the webhook HTTP API.

## Demo repos

See `demo_repos/README.md`. Three pushable repos exercise each failure class:
a bad dependency pin, a ruff lint violation, and an off-by-one test failure.
Each fails locally as designed:

- `scenario1` → `pip install -r requirements.txt` → *No matching distribution found for flask==0.0.1*
- `scenario2` → `ruff check .` → *Found 2 errors* (F401, F841)
- `scenario3` → `pytest` → *assert 5 == 4*

## Safety guarantees

- **Never auto-merges.** FixCI only opens a PR.
- **Never pushes to the original branch** — always a new `fixci/fix-<run_id>` branch.
- **Never opens a PR without a passing sandbox verification.** Otherwise it posts
  a diagnosis-only comment marked *"unverified — no patch pushed"*.
- **Loop-proof**: runs whose head branch starts with `fixci/` are ignored.
- **Deduplicates** deliveries by `run_id` (atomic claim in SQLite).
- **Redacts** tokens/keys/private keys from logs before sending them to the LLM.
- Rejects patches that touch `.github/`, secret-like files, paths outside the
  repo, or that exceed 200 changed lines.
- Sandbox runs with `mem_limit=1g`, a CPU quota, a hard `timeout`, and **no host
  filesystem mount**; the container is always torn down in a `finally` block
  (and the failing command is wrapped in `timeout`). The repo is cloned with a
  short-lived installation token passed via environment variable.

## Assumptions (decisions where the spec was ambiguous)

1. **SQLite via stdlib `sqlite3`** rather than SQLModel — simpler, fewer
   dependencies, and enough for run history.
2. **A rule-based classifier** (markers + failing command) rather than ML; it is
   intentionally simple and the LLM helps explain `unsupported` cases.
3. **Network isolation during verification is best-effort.** Dependencies must
   be installed with network on; FixCI then tries to detach the container from
   the default bridge before running the command. Some Docker platforms disallow
   that, in which case the step simply runs with network available.
4. **Commits use the GitHub Contents API** with the patched file contents read
   back from the sandbox, so FixCI never needs host git credentials.
5. **`workflow_run` only fires for workflows already present on the default
   branch** — that is a GitHub constraint, documented in the demo instructions.
6. **Comments target the PR** associated with the run (from the payload, or the
   open PR whose head is the failing branch). A failure on an unassociated
   branch is recorded in SQLite but produces no comment.

## Known limitations

- Only pytest-based Python projects; other ecosystems are out of scope.
- The sandbox installs `git` into the slim image at runtime (a few seconds per
  attempt); a purpose-built image with git preinstalled would be faster.
- One repository/installation per webhook delivery, as delivered by GitHub.

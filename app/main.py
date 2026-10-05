"""FastAPI application: webhook receiver + health check."""

from __future__ import annotations

import json
import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, status

from app.config import Settings, get_settings
from app.db import RunStore
from app.github_client import GithubClient
from app.llm import LLMClient
from app.models import WorkflowRunRef
from app.orchestrator import Orchestrator
from app.sandbox import Sandbox
from app.webhook import (
    extract_workflow_path,
    is_failed_workflow_run,
    is_fixci_branch,
    parse_workflow_run,
    verify_signature,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("fixci")

app = FastAPI(title="FixCI", version="0.1.0")

_settings: Settings = get_settings()
_store = RunStore(_settings.sqlite_path)
_store.init()


def build_orchestrator(run: WorkflowRunRef) -> Orchestrator:
    """Construct the pipeline dependencies for one run."""
    client = GithubClient(
        app_id=_settings.github_app_id,
        private_key=_settings.load_private_key(),
        installation_id=run.installation_id,
        repo_full_name=run.repo_full_name,
    )
    llm = LLMClient(api_key=_settings.anthropic_api_key, model=_settings.anthropic_model)
    sandbox = Sandbox(timeout=_settings.sandbox_timeout, enabled=_settings.sandbox_enabled)
    return Orchestrator(_settings, _store, client, llm, sandbox)


def process_run(run: WorkflowRunRef, workflow_path: str | None) -> None:
    """Background entry point: run the full pipeline for one failure."""
    try:
        orchestrator = build_orchestrator(run)
        record = orchestrator.process(run, workflow_path)
        logger.info("Run %s finished with status %s", run.run_id, record.status.value)
    except Exception:  # noqa: BLE001 - never let a background task crash silently
        logger.exception("Unhandled error processing run %s", run.run_id)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/runs")
def runs(limit: int = 20) -> list[dict]:
    """Recent run history (handy for the demo)."""
    return [r.model_dump(mode="json") for r in _store.recent(limit)]


@app.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
async def webhook(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict[str, str | int]:
    """Receive GitHub webhooks; verify, filter, dedupe, then process async."""
    body = await request.body()

    if not verify_signature(_settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signature")

    if x_github_event != "workflow_run":
        return {"status": "ignored", "reason": "not a workflow_run event"}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON payload") from None

    if not is_failed_workflow_run(payload):
        return {"status": "ignored", "reason": "not a completed failure"}

    run = parse_workflow_run(payload)
    if run is None:
        return {"status": "ignored", "reason": "missing run identifiers"}

    if is_fixci_branch(run.head_branch):
        return {"status": "ignored", "reason": "branch created by FixCI (loop prevention)"}

    if not _store.claim(run.run_id, run.repo_full_name):
        return {"status": "duplicate", "run_id": run.run_id}

    background.add_task(process_run, run, extract_workflow_path(payload))
    return {"status": "accepted", "run_id": run.run_id}

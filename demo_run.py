#!/usr/bin/env python3
"""
Local demo runner for FixCI.

This script simulates a FixCI run on one of the demo repositories
without needing a GitHub App or webhook setup. Perfect for demo videos.

Usage:
    python demo_run.py scenario1_bad_dependency
    python demo_run.py scenario2_lint
    python demo_run.py scenario3_offbyone
"""

import os
import sys
import subprocess
import tempfile
import shutil
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

from app.config import Settings, get_settings
from app.db import RunStore
from app.github_client import GithubClient
from app.llm import create_llm_client
from app.models import WorkflowRunRef
from app.orchestrator import Orchestrator
from app.sandbox import Sandbox
from app.log_analyzer import extract_failing_command, classify_failure, trim_log, redact_secrets

SCENARIOS = {
    "scenario1_bad_dependency": "demo_repos/scenario1_bad_dependency",
    "scenario2_lint": "demo_repos/scenario2_lint",
    "scenario3_offbyone": "demo_repos/scenario3_offbyone",
}


def run_command(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def setup_demo_repo(scenario_name: str) -> Path:
    """Copy demo repo to a temp directory and initialize git."""
    source = Path(SCENARIOS[scenario_name])
    if not source.exists():
        raise FileNotFoundError(f"Scenario not found: {source}")

    temp_dir = Path(tempfile.mkdtemp(prefix=f"fixci_demo_{scenario_name}_"))
    
    # Copy all files
    shutil.copytree(source, temp_dir, dirs_exist_ok=True)
    
    # Initialize git repo
    run_command(["git", "init"], temp_dir)
    run_command(["git", "config", "user.email", "demo@fixci.local"], temp_dir)
    run_command(["git", "config", "user.name", "FixCI Demo"], temp_dir)
    run_command(["git", "add", "."], temp_dir)
    run_command(["git", "commit", "-m", f"demo: {scenario_name} initial commit"], temp_dir)
    run_command(["git", "branch", "-M", "main"], temp_dir)
    
    return temp_dir


def simulate_failure(repo_path: Path) -> tuple[str, str]:
    """Run the failing command and capture output."""
    # Find the failing command from the workflow
    workflow_path = repo_path / ".github" / "workflows" / "ci.yml"
    
    # For demo, just run pytest directly (all scenarios use pytest)
    returncode, stdout, stderr = run_command(["pytest", "-q"], repo_path)
    
    combined = stdout + "\n" + stderr
    return combined, combined  # log, failing_command


def main():
    if len(sys.argv) < 2:
        print("Usage: python demo_run.py <scenario>")
        print(f"Available scenarios: {', '.join(SCENARIOS.keys())}")
        sys.exit(1)

    scenario = sys.argv[1]
    if scenario not in SCENARIOS:
        print(f"Unknown scenario: {scenario}")
        print(f"Available: {', '.join(SCENARIOS.keys())}")
        sys.exit(1)

    # Load settings from .env
    settings = get_settings()
    
    # Check for API key
    if not settings.openrouter_api_key and not settings.anthropic_api_key:
        print("ERROR: No LLM API key configured!")
        print("Set OPENROUTER_API_KEY or ANTHROPIC_API_KEY in .env")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"FixCI Demo: {scenario}")
    print(f"{'='*60}\n")

    # Setup demo repo
    print("📁 Setting up demo repository...")
    repo_path = setup_demo_repo(scenario)
    print(f"   Repo: {repo_path}")

    try:
        # Simulate the failure
        print("\n🔴 Running failing command to capture failure...")
        log_output, failing_cmd = simulate_failure(repo_path)
        print(f"   Command: {failing_cmd.strip()}")
        print(f"   Exit code: non-zero (failed)")
        print(f"   Log preview:\n{log_output[:500]}...")

        # Analyze the failure
        print("\n🔍 Analyzing failure...")
        failure_type = classify_failure(log_output, failing_cmd)
        trimmed_log = trim_log(log_output)
        print(f"   Classification: {failure_type.value}")

        # Create a mock workflow run reference
        run_ref = WorkflowRunRef(
            run_id=12345,
            repo_full_name=f"demo/{scenario}",
            head_branch="main",
            head_sha="abc123",
            installation_id=1,
        )

        # Build minimal GithubClient for demo (no real GitHub calls)
        class DemoGithubClient:
            def __init__(self, repo_path):
                self.repo_path = repo_path
                
            def installation_token(self):
                return "demo-token"
                
            def build_failure_context(self, run, workflow_path=None):
                from app.models import FailureContext
                from app.log_analyzer import (
                    trim_log, extract_failing_command, classify_failure, 
                    extract_referenced_files
                )
                
                diff = run_command(["git", "diff", "HEAD~1"], self.repo_path)[1]
                files = {}
                for f in self.repo_path.rglob("*.py"):
                    if f.is_file():
                        rel = f.relative_to(self.repo_path)
                        files[str(rel)] = f.read_text()
                
                # Use the same logic as the real GithubClient
                trimmed = trim_log(log_output)
                command = extract_failing_command(trimmed)
                classification = classify_failure(trimmed, command)
                
                # Get workflow YAML
                workflow_yaml = ""
                wf_path = self.repo_path / ".github/workflows/ci.yml"
                if wf_path.exists():
                    workflow_yaml = wf_path.read_text()
                
                # For test_failure and lint, get referenced files
                if classification.value in ("test_failure", "lint"):
                    for path in extract_referenced_files(trimmed):
                        full_path = self.repo_path / path
                        if full_path.exists():
                            files[path] = full_path.read_text()
                
                return FailureContext(
                    run=run,
                    trimmed_log=trimmed,
                    failure_type=classification,
                    failing_command=command,
                    diff=diff,
                    workflow_yaml=workflow_yaml,
                    files=files,
                )

        # Create orchestrator components
        print("\n🤖 Initializing FixCI pipeline...")
        client = DemoGithubClient(repo_path)
        llm = create_llm_client(settings)
        sandbox = Sandbox(timeout=settings.sandbox_timeout, enabled=settings.sandbox_enabled)
        store = RunStore(settings.sqlite_path)
        store.init()
        
        orchestrator = Orchestrator(settings, store, client, llm, sandbox)

        # Run the pipeline
        print("\n🚀 Running FixCI pipeline...")
        print("   Step 1: Diagnosing failure with LLM...")
        print("   Step 2: Generating patch...")
        print("   Step 3: Verifying patch in Docker sandbox...")
        print("   Step 4: Creating fix PR (simulated)...")
        
        record = orchestrator.process(run_ref)
        
        print(f"\n✅ Pipeline completed!")
        print(f"   Status: {record.status.value}")
        print(f"   Attempts: {record.attempts}")
        if record.root_cause:
            print(f"   Root cause: {record.root_cause}")
        if record.pr_url:
            print(f"   PR URL: {record.pr_url}")
        if record.detail:
            print(f"   Detail: {record.detail}")

        # Show recent runs
        print(f"\n📊 Recent runs:")
        for r in store.recent(5):
            print(f"   Run {r.run_id}: {r.status.value} ({r.repo_full_name})")

    finally:
        # Cleanup
        print(f"\n🧹 Cleaning up temp directory...")
        shutil.rmtree(repo_path, ignore_errors=True)

    print(f"\n{'='*60}")
    print("Demo complete!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
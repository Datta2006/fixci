# FixCI demo repos

Three minimal, deliberately-broken repositories that exercise the three
supported failure classes. Each one is a self-contained folder you can push to
its own GitHub repository.

| Folder | Failure class | What breaks |
| --- | --- | --- |
| `scenario1_bad_dependency/` | dependency | `flask==0.0.1` does not exist on PyPI |
| `scenario2_lint/` | lint | `ruff check .` finds unused imports/variables |
| `scenario3_offbyone/` | test_failure | `add(2, 2)` returns `5` instead of `4` |

## Setup (once per scenario)

1. Create a new **public** GitHub repository (e.g. `fixci-demo-1`).
2. Install the FixCI GitHub App on that repository
   (see the top-level `README.md` for App creation).
3. Push the scenario contents as the initial commit:

   ```bash
   cd demo_repos/scenario1_bad_dependency
   git init
   git add .
   git commit -m "demo: broken dependency pin"
   git branch -M main
   git remote add origin git@github.com:<you>/fixci-demo-1.git
   git push -u origin main
   ```

4. The push to `main` runs the workflow and fails. `workflow_run` only fires
   after the workflow has run once, so the initial failing run seeds the demo.

## Trigger FixCI

FixCI listens on `workflow_run` (completed, failure). To make it act on a
pull request, open one that changes a file — the workflow runs on the PR head
and, when it fails, FixCI diagnoses it:

```bash
git checkout -b feature/touch
echo "# touch" >> README.md
git commit -am "trigger ci"
git push -u origin feature/touch
gh pr create --fill
```

Expected outcomes:

- **Scenario 1 & 3** — FixCI finds a patch, verifies it in the Docker sandbox,
  and opens a PR on a `fixci/fix-<run_id>` branch with the passing output as
  evidence.
- **Scenario 2** — same flow; the patch removes the unused import.
- If a patch cannot be verified within `MAX_ATTEMPTS`, FixCI posts a
  **diagnosis-only comment** on the PR and pushes nothing.

## Notes

- `workflow_run` events are only delivered for workflows that already exist on
  the default branch, hence the initial push in step 3.
- FixCI ignores any branch starting with `fixci/`, so its own PRs never
  re-trigger it (no infinite loops).
- These repos are intentionally broken; do not reuse their dependency pins.

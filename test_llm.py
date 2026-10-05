import os
os.environ['LOG_LEVEL'] = 'DEBUG'
import logging
logging.basicConfig(level=logging.DEBUG, format='%(name)s - %(levelname)s - %(message)s')
from app.config import get_settings
from app.llm import create_llm_client, build_user_prompt
from app.models import FailureContext, WorkflowRunRef, FailureType
import tempfile
import shutil
from pathlib import Path
import subprocess

settings = get_settings()
llm = create_llm_client(settings)

run = WorkflowRunRef(run_id=12345, repo_full_name='demo/scenario2_lint', head_branch='main', head_sha='abc123', installation_id=1)

source = Path('demo_repos/scenario2_lint')
temp_dir = Path(tempfile.mkdtemp(prefix='test_'))
shutil.copytree(source, temp_dir, dirs_exist_ok=True)

result = subprocess.run(['git', 'diff', 'HEAD~1'], cwd=temp_dir, capture_output=True, text=True)
diff = result.stdout

files = {}
for f in temp_dir.rglob('*.py'):
    if f.is_file():
        rel = f.relative_to(temp_dir)
        files[str(rel)] = f.read_text()

trimmed_log = (
    "F401 [*] `os` imported but unused\n"
    " --> app.py:5:8\n"
    "  |\n"
    "3 | from __future__ import annotations\n"
    "4 |\n"
    "5 | import os\n"
    "  |        ^^\n"
    "help: Remove unused import: `os`\n"
    "  |\n"
    "4 |\n"
    "  - import os\n"
    "5 |\n"
    "\n"
    "F841 Local variable `unused` is assigned to but never used\n"
    "  --> app.py:10:5\n"
    "   |\n"
    " 8 | def greet(name: str) -> str:\n"
    " 9 |     \"\"\"Return a greeting.\"\"\"\n"
    "10 |     unused = \"this variable is never read\"\n"
    "   |     ^^^^^^\n"
    "11 |     return f\"Hello, {name}!\"\n"
    "   |\n"
    "help: Remove assignment to unused variable `unused`"
)

context = FailureContext(
    run=run,
    trimmed_log=trimmed_log,
    failure_type=FailureType.lint,
    failing_command='ruff check .',
    diff=diff,
    workflow_yaml='name: CI\non:\n  push:\n    branches: [main]\n  pull_request:\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - uses: actions/setup-python@v5\n        with:\n          python-version: "3.12"\n      - name: Install dependencies\n        run: pip install -r requirements.txt\n      - name: Lint\n        run: ruff check .\n      - name: Run tests\n        run: pytest -q',
    files=files,
)

prompt = build_user_prompt(context)
print('Prompt length:', len(prompt))
print('---PROMPT---')
print(prompt[:1000])
print('...')
print('---END PROMPT---')

raw = llm._complete(prompt)
print('---RAW RESPONSE---')
print(repr(raw))
print('---END RAW RESPONSE---')

shutil.rmtree(temp_dir, ignore_errors=True)
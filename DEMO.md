# FixCI Demo Guide

This guide shows how to run the FixCI demo locally for video recording or testing.

## Quick Start

### 1. Install dependencies

```bash
cd /Users/apple/Documents/4thyear/iqoo/fixci

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install requirements
pip install -r requirements.txt
```

### 2. Configure API keys

Copy the example env file and add your API key:

```bash
cp .env.example .env
```

Edit `.env` and add **one** of the following:

**Option A: OpenRouter (recommended - supports many models)**
```bash
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
# Optional: Use a different model
# OPENROUTER_MODEL=openai/gpt-4o
# OPENROUTER_MODEL=meta-llama/llama-3.1-405b-instruct
```

**Option B: Anthropic directly**
```bash
ANTHROPIC_API_KEY=sk-ant-your-key-here
ANTHROPIC_MODEL=claude-sonnet-4-5
```

Get your OpenRouter API key at: https://openrouter.ai/keys

### 3. Run a demo scenario

```bash
# Scenario 1: Bad dependency (flask==0.0.1 doesn't exist)
python demo_run.py scenario1_bad_dependency

# Scenario 2: Lint error (unused imports)
python demo_run.py scenario2_lint

# Scenario 3: Off-by-one test failure (2+2=5)
python demo_run.py scenario3_offbyone
```

## What the demo does

The `demo_run.py` script:
1. **Copies** the scenario to a temp directory
2. **Initializes git** with a commit
3. **Runs the failing command** (pytest) to capture the failure
4. **Analyzes** the failure type (dependency/lint/test)
5. **Calls the LLM** (via OpenRouter or Anthropic) to diagnose and generate a patch
6. **Validates** the patch
7. **Runs verification** in an isolated Docker sandbox
8. **Reports** the result (simulated PR creation)

## Expected outputs

| Scenario | Failure Type | Expected Fix |
|----------|-------------|--------------|
| `scenario1_bad_dependency` | dependency | Fix flask version pin |
| `scenario2_lint` | lint | Remove unused imports |
| `scenario3_offbyone` | test_failure | Fix the off-by-one bug |

## For demo video recording

### Option 1: Terminal recording (recommended)
Use `asciinema` for clean terminal recordings:

```bash
pip install asciinema
asciinema rec fixci_demo.cast
# Run demo commands
# Press Ctrl+D to stop
asciinema play fixci_demo.cast
```

### Option 2: Screen recording
Use any screen recorder (OBS, QuickTime, etc.) and run the demo in terminal.

### Option 3: Run the full webhook server
For a more complete demo showing the webhook flow:

```bash
# Terminal 1: Start the server
uvicorn app.main:app --reload --port 8000

# Terminal 2: Expose with ngrok (for GitHub webhook)
ngrok http 8000
# Use the ngrok URL + /webhook as your GitHub App webhook URL

# Terminal 3: Trigger a failure in a demo repo
cd demo_repos/scenario1_bad_dependency
git checkout -b feature/touch
echo "# trigger" >> README.md
git commit -am "trigger ci"
git push -u origin feature/touch
gh pr create --fill
```

## Docker setup (for sandbox verification)

The sandbox requires Docker to be running:

```bash
# macOS: Docker Desktop
# Linux: sudo systemctl start docker

# Verify
docker run hello-world
```

If Docker isn't available, set `SANDBOX_ENABLED=false` in `.env` to run in diagnosis-only mode.

## Troubleshooting

### "No LLM API key configured"
Make sure `.env` has either `OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY` set.

### Docker permission errors
```bash
# Linux: add user to docker group
sudo usermod -aG docker $USER
# Log out and back in
```

### "Could not parse diagnosis"
The LLM sometimes returns malformed JSON. The system retries once automatically. If it persists, try a different model in `.env`.

### Import errors
```bash
pip install -r requirements.txt --upgrade
```

## Demo tips for video

1. **Show the broken code first** - open the scenario files before running
2. **Explain the failure** - show the pytest output
3. **Run the demo** - let it run through the full pipeline
4. **Show the result** - the fixed code and verification output
5. **Mention key features**:
   - Never pushes unverified patches
   - Uses Docker sandbox for verification
   - Retries up to MAX_ATTEMPTS times
   - Falls back to diagnosis-only comment

## File structure for reference

```
fixci/
├── demo_run.py           # Local demo runner (use this!)
├── demo_repos/
│   ├── scenario1_bad_dependency/
│   ├── scenario2_lint/
│   └── scenario3_offbyone/
├── app/
│   ├── llm.py            # LLM clients (Anthropic + OpenRouter)
│   ├── orchestrator.py   # Main pipeline
│   ├── sandbox.py        # Docker verification
│   └── ...
├── .env                  # Your API keys (not committed)
├── .env.example          # Template
└── requirements.txt
```

## Using OpenRouter models

OpenRouter gives you access to many models. Popular choices:

| Model | Use Case |
|-------|----------|
| `anthropic/claude-3.5-sonnet` | Best for code (default) |
| `anthropic/claude-3-haiku` | Fast, cheaper |
| `openai/gpt-4o` | Strong reasoning |
| `openai/gpt-4o-mini` | Fast, cheap |
| `meta-llama/llama-3.1-405b-instruct` | Open source, large |
| `google/gemini-pro-1.5` | Large context |

Set in `.env`:
```bash
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
```
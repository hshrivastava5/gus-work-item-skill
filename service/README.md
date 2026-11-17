# Altura → GUS Results Mapping Service

A local web service that lets you analyze an Altura suite run and push results into GUS (TS-1218138) with one click.

## What it does

- Paste an Altura run URL → see all SCN results, verdicts, failure details
- One-click submit to GUS: creates a Suite Execution + Test Executions for the correct PFG
- Performance details stored in each Test Execution's Comments field

## Prerequisites (once per machine)

1. **uv** (Python runner)
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **altura-sdk** at `~/altura-sdk` (or set `ALTURA_SDK_PATH`)
   ```bash
   git clone <altura-sdk-repo> ~/altura-sdk
   ```

3. **Altura TLS certificate** at `~/.altura/combined-ca.pem`
   ```bash
   mkdir -p ~/.altura
   # Copy combined-ca.pem from a teammate or Altura onboarding docs
   ```

4. **GUS org authenticated**
   ```bash
   sf org login web --instance-url https://gus.my.salesforce.com --alias gus
   ```

## Start the service

```bash
bash service/start.sh
```

Then open **http://localhost:8765** in your browser.

If your altura-sdk is not at `~/altura-sdk`:
```bash
export ALTURA_SDK_PATH=/path/to/your/altura-sdk
bash service/start.sh
```

## Files

| File | Purpose |
|------|---------|
| `server.py` | FastAPI backend — Altura API calls, GUS composite API, Postgres queries |
| `static/index.html` | Single-page UI — run input, SCN results table, GUS submit form |
| `start.sh` | Launcher script using `uv run` (no venv needed) |

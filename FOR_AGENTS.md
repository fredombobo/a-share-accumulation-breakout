# For AI Agents — Zero-friction install & run

**User only supplies:** `TUSHARE_TOKEN`  
**Agent does everything else.**

## Canonical command

```bash
git clone https://github.com/fredombobo/a-share-accumulation-breakout.git
cd a-share-accumulation-breakout
python bootstrap.py --token "<USER_TUSHARE_TOKEN>" --yes --no-browser
```

Equivalent via env (preferred so token is less likely to land in shell history on some systems):

```bash
export TUSHARE_TOKEN="<USER_TUSHARE_TOKEN>"
python bootstrap.py --yes --no-browser
```

Windows PowerShell:

```powershell
git clone https://github.com/fredombobo/a-share-accumulation-breakout.git
cd a-share-accumulation-breakout
$env:TUSHARE_TOKEN = "<USER_TUSHARE_TOKEN>"
python bootstrap.py --yes --no-browser
```

## What bootstrap does

1. Writes `.env` with the token (gitignored)
2. `pip install -r requirements.txt`
3. Incremental market sync into local SQLite (`runtime/`, gitignored)
4. Starts FastAPI on **:8000** serving the built React UI (`web/frontend/dist`)
5. Prints machine-readable: `BOOTSTRAP_OK url=http://127.0.0.1:8000/`

## Success check

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/setup-status
```

## User next click (not agent)

Open `http://127.0.0.1:8000/` → button **扫描** → wait ~5–15 min → read **A 池**.

## Flags

| Flag | Meaning |
|------|---------|
| `--token` | Tushare token |
| `--yes` / `-y` | Non-interactive (required for agents) |
| `--no-browser` | Do not open browser |
| `--skip-sync` | Skip market download (dev only) |
| `--sync-days N` | Override history window |
| `--foreground` | Block in foreground (long-running child) |
| `--install-only` | Only deps + .env |

## Stop

```powershell
# Windows
.\stop_ui.ps1
# or
.\停止.bat
```

## Do not

- Commit `.env` or `runtime/`
- Echo the raw token into chat logs if avoidable
- Expect A-pool stocks in defense regime (empty A is OK)

## Human paste template

See [PROMPT_FOR_AGENT.md](./PROMPT_FOR_AGENT.md).

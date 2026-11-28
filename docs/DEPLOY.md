# Deployment

RoadPulse is one process and one SQLite file. Run **one** instance. Pick a route:

| Route | Use it for | Status in this project |
|---|---|---|
| Local Python | a laptop demo | tested: tests, demo scripts and browser tests all run this way |
| Docker / compose | any Docker host | **configuration reviewed, image not built here** (Docker was not available). The CI workflow builds it and runs a health check on every push: check that run |
| Render (free) | a public link for judges | **prepared, not deployed**: no account or credentials were available. Steps below |

## 1. Local (fresh machine)

Python 3.12 or newer. No Node.js.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env       # then edit the logins, keys and secret
uvicorn app.main:app --app-dir backend --env-file .env --no-access-log --timeout-graceful-shutdown 3
```

macOS and Linux: `python -m venv .venv && source .venv/bin/activate`, `cp .env.example .env`, same `pip` and `uvicorn` lines.
Open `http://localhost:8000`. The first start creates `data/roadpulse.db` and loads `data/area.graphml` (a few seconds).
Check `http://localhost:8000/api/health`: `"map_mode":"osm"` and `"map_warning":null`.

**Restart and shutdown:** Ctrl+C stops it cleanly (the database connection is closed). On the next start open incidents
and subscribers are reloaded from SQLite. Officers stay signed in across a restart only if `ROADPULSE_SECRET` is set.

## 2. Docker

```bash
cp .env.example .env              # edit it first
docker compose up --build         # http://localhost:8000
```

The image is `python:3.12-slim`, runs as a non-root user, listens on `$PORT` (default 8000), and has a health check on
`/api/health`. The image contains `backend/` and `data/` (including the real map); `.dockerignore` keeps `.git`, `.env`,
virtual environments and database files out. The database lives in the named volume `roadpulse-data`.

## 3. Render (free web service)

1. render.com, **New +**, **Web Service**, connect the GitHub repository (or paste the public repository URL).
2. **Runtime** Docker, **Branch** `main`, **Region** Singapore, **Instance type** Free, **Health Check Path** `/api/health`.
3. Environment variables (generate every secret yourself; never commit them):

| Name | Value |
|---|---|
| `ROADPULSE_SECRET` | 48 random hex characters |
| `OFFICER_USERS` | `officer:YOUR-PASSPHRASE` (no commas or colons in the passphrase) |
| `POLICE_USERS` | `unit1:ANOTHER-PASSPHRASE,unit2:...` |
| `DEVICE_KEYS` | a random key (the simulator and a sensor send it as `X-Device-Key`) |
| `TRUST_PROXY` | `1` (Render is a reverse proxy; without it all visitors share one rate limit) |
| `API_DOCS` | `0` to hide the interactive API reference on a public server (optional) |

Generate a secret in PowerShell: `$b = New-Object byte[] 24; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); ($b | ForEach-Object { $_.ToString('x2') }) -join ''`

4. After the first build, open `https://<name>.onrender.com/api/health` and check `"map_mode":"osm"`.
5. Run the demo against it: `python simulator/demo.py story --url https://<name>.onrender.com --device-key <key> --user officer --password <passphrase>`.

Free-plan limits: the service sleeps after about 15 minutes idle (the first request then takes about a minute), and data
resets on every restart or redeploy because there is no persistent disk. Open the link before you present, and re-run
the simulator to rebuild the scenario (under a minute).

## Checklist before it goes public

- [ ] no `change-me` left in `.env` or in the host's variables (the server logs a warning for each one)
- [ ] `TRUST_PROXY=1` only behind a proxy, `0` otherwise
- [ ] `.env` is not in the repository or in any zip you share
- [ ] `/api/health` shows the real map and no warning
- [ ] the Actions run on GitHub is green (tests, and the Docker build and health check)
- [ ] you have said out loud that the sensor data, rain and boards are simulated

## Backup and data

`data/roadpulse.db` is the only state. Copy it while the server is stopped. Raw sensor readings are deleted after 7 days,
raw reports after 30, cleared incidents and inactive subscribers after 30; the audit log is kept.

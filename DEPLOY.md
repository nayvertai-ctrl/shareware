# Deploying shareware (free, no credit card)

Recommended host: **PythonAnywhere** free "Beginner" account — no card, persistent
disk (so SQLite just works), free HTTPS, no Docker. Perfect for a family-scale app.

## What "deploy-ready" means here

The repo already includes:
- `wsgi.py` — production entry point. Creates tables if missing, **never wipes data**
  (unlike `python3 app.py`, which re-seeds demo data and is for local dev only).
- `requirements.txt` — just `Flask`.
- `DB_PATH` env var support; by default the SQLite file lives next to `app.py`, on
  PythonAnywhere's persistent disk — no config needed.

The deployed app starts **empty** (no Anna/Bob demo data): you and your family just
sign up at the URL. Accounts + membership authz mean it's safe to expose publicly.

---

## Checklist

### 0. Get the code onto GitHub
The steps below `git clone` the repo, so the deploy changes must be pushed first.
The repo `nayvertai-ctrl/shareware` is **private**, so on PythonAnywhere either:
- **Make it public** (the code has no secrets), then clone the plain HTTPS URL; or
- Keep it private and clone with a **GitHub personal access token**:
  `git clone https://<TOKEN>@github.com/nayvertai-ctrl/shareware.git`

### 1. Create the account
- Sign up at **pythonanywhere.com** → "Create a Beginner account" (free, email only).

### 2. Clone + install (Bash console)
On the dashboard: **Consoles → Bash**, then:
```bash
git clone https://github.com/nayvertai-ctrl/shareware.git
cd shareware
pip install --user -r requirements.txt
```

### 3. Create the web app
- **Web** tab → **Add a new web app** → **Manual configuration** → **Python 3.x**
  (pick the version `python3 --version` showed in the console).

### 4. Point it at the app
In the **Web** tab:
- **Source code**: `/home/YOURUSERNAME/shareware`
- **Working directory**: `/home/YOURUSERNAME/shareware`
- Click the **WSGI configuration file** link and replace its entire contents with:
  ```python
  import sys
  path = "/home/YOURUSERNAME/shareware"
  if path not in sys.path:
      sys.path.insert(0, path)
  from wsgi import application  # noqa: F401
  ```
  (Replace `YOURUSERNAME` in both files.)

### 5. Go live
- Click the big green **Reload** button.
- Open **https://YOURUSERNAME.pythonanywhere.com** — you should see the login screen.
- Click **Create an account**, sign up, create a group, and share the URL with family.
  Each person signs up themselves, or you send them a group **invite link** from the app.

---

## Operating it

- **HTTPS** is automatic on the `pythonanywhere.com` subdomain.
- **Keep-alive**: free web apps ask you to click a "run until 3 months from now"
  button every ~3 months (they email a reminder). One click keeps it running.
- **Updating after code changes**:
  ```bash
  cd ~/shareware && git pull
  ```
  then **Reload** in the Web tab.
- **Backups**: the whole database is one file — `~/shareware/splitwise.db`. Download it
  occasionally from the **Files** tab to keep a backup.
- **Data safety**: `wsgi.py` only ever runs `CREATE TABLE IF NOT EXISTS`. Never run
  `python3 app.py` (plain) on the server — that path re-seeds and wipes. Use it only
  on your own machine for local testing.

## If you outgrow the free tier

Move the data to a free **Neon** or **Supabase** Postgres (also no card) and host the
app on **Koyeb** free (no card, no sleep). That needs porting `sqlite3` → `psycopg`.
Not necessary for friends-and-family scale.

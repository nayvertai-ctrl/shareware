# Self-host via Cloudflare Tunnel (free, no card, no rewrite)

Run `shareware` **as-is** on a machine you own; Cloudflare Tunnel exposes it at your
own domain with automatic HTTPS, no open ports, and free DDoS/WAF. The app and its
SQLite database live entirely on your machine.

**You need:** an always-on machine (home server, Raspberry Pi, spare laptop, an
always-on Mac) and a domain already on Cloudflare (free plan is fine). The app is
only reachable while that machine is on.

This guide uses the existing **nayvertai** Cloudflare account and the
**gopiramsarees.in** zone, on a dedicated subdomain **`split.gopiramsarees.in`**.
Routing a subdomain adds one proxied CNAME and does **not** touch the live
`gopiramsarees.in` site — never point the tunnel at the apex/root domain.

---

## Part A — Run the app as a real server

On the always-on machine:

```bash
git clone https://github.com/nayvertai-ctrl/shareware.git
cd shareware
pip install -r requirements.txt gunicorn        # waitress instead of gunicorn on Windows
```

Start it bound to localhost (the tunnel reaches it locally — never expose it directly):

```bash
# Linux / macOS
gunicorn wsgi:application -b 127.0.0.1:5000 --workers 2

# Windows
# waitress-serve --listen=127.0.0.1:5000 wsgi:application
```

`wsgi.py` runs `init_db()` on startup — it creates tables if missing and **never
wipes data**. Do NOT run `python3 app.py` on the server: that path re-seeds demo data
and erases the database. Test locally: `curl -s localhost:5000/ | head -c 40` should
return HTML.

> Optional: put the database on a stable path with `export DB_PATH=/home/you/shareware/splitwise.db`
> before starting (the default already sits in the repo dir, which is fine).

## Part B — Create the tunnel

Install cloudflared: `brew install cloudflared` (macOS) · `sudo apt install cloudflared`
or download the `.deb`/binary from Cloudflare (Linux) · winget/MSI (Windows).

```bash
cloudflared tunnel login                 # browser — pick the nayvertai account + gopiramsarees.in
cloudflared tunnel create shareware      # creates a tunnel + a credentials .json; note the Tunnel ID
cloudflared tunnel route dns shareware split.gopiramsarees.in   # your chosen subdomain
```

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL_ID>                       # from `tunnel create`
credentials-file: /home/YOU/.cloudflared/<TUNNEL_ID>.json
ingress:
  - hostname: split.gopiramsarees.in
    service: http://localhost:5000
  - service: http_status:404
```

Run it:

```bash
cloudflared tunnel run shareware
```

Open **https://split.gopiramsarees.in** — the login screen should load over HTTPS.
Sign up, create a group, and share the URL (or in-app invite links) with family.

## Part C — Keep it running (auto-start on boot)

**Tunnel as a service** (both survive reboots):
```bash
sudo cloudflared service install         # runs the tunnel from config.yml on boot
```

**App as a service** — Linux `systemd` example (`/etc/systemd/system/shareware.service`):
```ini
[Unit]
Description=shareware
After=network.target
[Service]
WorkingDirectory=/home/YOU/shareware
ExecStart=/usr/bin/gunicorn wsgi:application -b 127.0.0.1:5000 --workers 2
Restart=always
User=YOU
[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now shareware
```
On macOS use a `launchd` plist (or just run both in a `tmux`/`screen` session for a
simpler, non-boot-persistent setup). Windows: run gunicorn→waitress + cloudflared as
Services (e.g. via NSSM).

---

## Quick 30-second test (no domain, ephemeral URL)

To try it before wiring your domain, skip Part B and just run:
```bash
cloudflared tunnel --url http://localhost:5000
```
It prints a random `https://<random>.trycloudflare.com` URL that works immediately.
The URL changes each run and isn't stable — use the named tunnel above for real use.

## Notes

- **Security:** the app has its own accounts + membership authorization, so public
  exposure is safe. For an extra gate you can put **Cloudflare Access** (Zero Trust,
  free tier) in front of the hostname — optional; the app's auth already suffices.
- **Updates:** `cd ~/shareware && git pull` then restart the app service.
- **Backups:** the whole database is one file — `~/shareware/splitwise.db`. Copy it
  somewhere safe periodically.

# Deploying to a VPS

Runs the bot 24/7 under `systemd`, restarting on crash and on reboot. Written
for a fresh Debian/Ubuntu box (Vultr, Hetzner, DigitalOcean, Oracle Cloud).

---

## 0. Secure the server first

A new VPS with password login on a public IP starts getting brute-forced within
hours. Do this before anything else.

From your Windows machine, create a key if you don't have one and copy it up:

```powershell
ssh-keygen -t ed25519            # skip if ~/.ssh/id_ed25519.pub exists
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@YOUR_IP "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Confirm `ssh root@YOUR_IP` logs in **without** asking for a password, then turn
password auth off:

```bash
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl restart ssh
```

Keep your current session open while you test a second one — locking yourself
out here means a console rescue.

If the root password has ever been shown on screen, screenshotted, or pasted
anywhere, rotate it in your provider's dashboard too. Disabling password auth
stops it working over SSH, but it still works at the provider's web console.

---

## 1. Get the code onto the server

The repo has no remote yet. Either push it to a private GitHub repo:

```powershell
git init
git add -A
git commit -m "Initial commit"
gh repo create whale-bot --private --source=. --push
```

...then on the server:

```bash
git clone https://github.com/YOUR_USER/whale-bot.git /root/whale-src
```

Or skip GitHub entirely and copy it straight up:

```powershell
scp -r . root@YOUR_IP:/root/whale-src
```

`.env` and `accounts.db` are gitignored, so neither travels with the code.
That is deliberate - both hold live credentials. You set them up on the server.

---

## 2. Install

```bash
cd /root/whale-src
bash deploy/setup.sh
```

This installs Python, creates an unprivileged `whale` user, builds a virtualenv
in `/opt/whale/.venv`, and installs the systemd unit. It is safe to re-run.

---

## 3. Configure

```bash
nano /opt/whale/.env
```

Set `TELEGRAM_BOT_TOKEN`. The bot exits immediately without it.

Optionally set `TWITTER_AUTH_TOKEN` and `TWITTER_CT0` - with both present the
bot re-registers the X session on every start, so the login survives a reboot
or redeploy even if `accounts.db` is lost. Otherwise just run `/auth` in
Telegram once after starting.

```bash
systemctl restart whale-bot
journalctl -u whale-bot -f
```

---

## 4. Verify

You should see `Bot is starting polling...` and no `Conflict` errors.

**Stop the bot on your Windows machine before starting it here.** Telegram
permits exactly one `getUpdates` consumer per token; two instances produce the
409 `Conflict: terminated by other getUpdates request` loop.

Then message the bot on Telegram and send `/auth_status`.

---

## Adding X accounts (deeper coverage on big threads)

A single X account can only page a few hundred tweets per 15-minute rate window,
so very large *fresh* threads come back partial. The bot rotates through every
account in its pool automatically, so adding more accounts pages deeper.

- Send `/auth <auth_token> <ct0>` **once per account.** Each distinct account is
  added to the pool; re-sending the same account just refreshes its cookies.
- The bot resolves and shows each account's real `@handle`, and rejects invalid
  or expired cookies on the spot.
- `/auth_status` lists every account with its state — `✅ active`,
  `🔒 throttled until HH:MM`, or `❌ expired` — so you can see which throwaway
  got rate-limited.
- Use **throwaway accounts**. Heavy reply-scraping can get an account
  rate-limited hard or suspended; never use anything you care about.

`/login` (interactive username/password) also accumulates and, on success, backs
itself up with its own cookies — so a `/login` account is cookie-backed too and
survives a DB wipe. Note `/login` uses X's password endpoint, which Cloudflare
often blocks from datacenter IPs; on a VPS prefer `/auth`, or set `TWS_PROXY`.

Sessions persist in `/opt/whale/.sessions.json` (owner-only, gitignored) and are
restored on every boot, so they survive a restart or an `accounts.db` wipe. The
legacy single `TWITTER_AUTH_TOKEN`/`TWITTER_CT0` env pair still works and is
migrated into the store on first boot.

---

## Updating

```bash
cd /root/whale-src && git pull
bash deploy/setup.sh
systemctl restart whale-bot
```

`setup.sh` excludes `accounts.db` and `.env` from the sync, so an update never
wipes your session or config.

---

## Day-to-day

| Task | Command |
| --- | --- |
| Status | `systemctl status whale-bot` |
| Live logs | `journalctl -u whale-bot -f` |
| Recent errors | `journalctl -u whale-bot -p err -n 50` |
| Restart | `systemctl restart whale-bot` |
| Stop | `systemctl stop whale-bot` |
| Disable at boot | `systemctl disable whale-bot` |

You do not need to install anything on your phone. The bot is a Telegram bot,
so Telegram is the mobile interface. For server access from a phone, any SSH
client (Termux, JuiceSSH, Blink) works with the key you generated above.

---

## A note on X and datacenter IPs

X ranks datacenter IP ranges worse than residential ones, so a cookie session
used from a VPS may get challenged sooner than the same session used at home.
If `/auth_status` starts reporting `SESSION EXPIRED` frequently, route X traffic
through a residential proxy by setting `TWS_PROXY` in `.env`:

```env
TWS_PROXY=http://user:pass@host:port
```

This affects only X. Telegram traffic is unaffected.

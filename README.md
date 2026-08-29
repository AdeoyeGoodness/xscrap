# Telegram Lead Enrichment Bot 🚀

A modular Telegram bot that extracts **usernames** from social media links. Send it a post link and it replies with a plain numbered list of handles - the post author plus, when you are logged in, every commenter. Starting with **Twitter / X**, with a plugin architecture for adding LinkedIn, Instagram, TikTok, and YouTube extractors.

---

## 📁 Project Structure

```
WHALE/
├── .env.example              # Sample environment configuration file
├── .gitignore                # Git ignore rules
├── requirements.txt          # Python dependencies
├── README.md                 # Documentation
├── src/
│   ├── __init__.py
│   ├── config.py             # Configuration & environment loader
│   ├── bot.py                # Main Telegram bot executable
│   ├── extractors/           # Social media link extractors (Plugin Architecture)
│   │   ├── __init__.py
│   │   ├── base.py           # Abstract Base Class for extractors
│   │   └── twitter.py        # Twitter / X link & handle extractor
│   └── services/
│       ├── __init__.py
│       ├── auth_service.py   # Twitter / X login session management (twscrape pool)
│       └── enrichment.py     # Username extraction & message parsing service
└── tests/
    ├── conftest.py               # Offline stubs - tests never hit the network
    ├── test_bot_formatting.py    # Reply rendering & message chunking
    └── test_twitter_extractor.py # Automated unit tests for Twitter extraction
```

---

## ⚙️ Quick Setup Guide

### 1. Configure Telegram Bot Token
1. Open Telegram and search for `@BotFather`.
2. Send `/newbot` and follow the instructions to get your Telegram Bot API Token.
3. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
4. Open `.env` and paste your Bot Token:
   ```env
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
   ```

### 2. Run the Bot
Execute the bot script:
```bash
python -m src.bot
```

---

## 🧪 Running Unit Tests

Run `pytest` to execute automated tests for link parsing and username extraction:

```bash
python -m pytest tests/
```

---

## 🔑 Twitter / X Authentication

Commenter extraction needs a logged-in X session. Two ways to get one:

| Command | Notes |
| --- | --- |
| `/auth <auth_token> <ct0>` | **Recommended.** Cookie auth. Both cookies are required - X rejects any request whose `ct0` does not match the `auth_token`, so a session registered without a real `ct0` fails every call silently. |
| `/login` | Interactive 3-step login (username → password → email). The password message is deleted immediately. Often blocked - see below. |

Check state with `/auth_status`, and clear it with `/logout`.

### Getting your cookies for `/auth`

1. Log into `x.com` in your browser.
2. Open DevTools → **Application** → **Cookies** → `https://x.com`.
3. Copy the values of `auth_token` and `ct0`.
4. Send `/auth <auth_token> <ct0>`. The bot deletes your message right after reading it.

### If `/login` fails

X protects the password-login endpoint (`onboarding/task.json?flow_name=login`)
with Cloudflare and frequently returns `403 Sorry, you have been blocked` before
the login form is ever reached. That is a network-level block on your IP, not a
credential problem, and no code change gets past it. Either use `/auth` above,
or route traffic through a proxy by setting `TWS_PROXY` in `.env`:

```env
TWS_PROXY=http://user:pass@host:port
```

---

## 💡 How It Works

1. Send any message containing Twitter/X links or `@handles` to your Telegram bot:
   - `https://x.com/elonmusk`
   - `https://twitter.com/sama`
   - `https://x.com/naval/status/1234567890`
   - `@levelsio`
2. `EnrichmentService` hands the whole message to each extractor exactly once.
3. `TwitterExtractor` collects every username referenced: profile/status URL authors, intent links, and `@mentions`.
4. If the message contains a post link **and** an account is logged in, it also pulls the usernames of everyone who replied to that post.
5. The bot replies with a deduplicated numbered list of usernames - nothing else. No names, follower counts, bios, or avatars. Long lists are split across messages to stay under Telegram's 4096-character limit.

Without a logged-in session a post link yields just the author, and the bot says so rather than failing quietly.

### Session expiry

X cookie sessions do not last forever. When one dies mid-request, twscrape
deactivates the account and the bot replies:

> 🔒 **Your Twitter session expired.** The list above is missing this post's
> commenters. Re-authenticate with `/auth <auth_token> <ct0>` and send the link
> again.

This is deliberately distinct from a post that genuinely has no replies - both
produce an empty commenter list, and conflating them makes a dead login look
like a quiet post. `/auth_status` spends one cheap request to confirm the
session really works, rather than trusting the `active` flag in `accounts.db`,
which stays set until something tries to use the cookie.

---

## 🔌 Adding New Platforms

To add a new platform (e.g. LinkedIn or Instagram):
1. Subclass `BaseExtractor` in `src/extractors/<platform>.py`.
2. Implement `can_handle(text)` and `extract_username(text)`.
3. Register the extractor in `EnrichmentService` (`src/services/enrichment.py`).

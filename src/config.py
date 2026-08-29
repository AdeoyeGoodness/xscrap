import os
import re
import json
import stat
import asyncio
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Optional
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables from .env file
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TWITTER_AUTH_TOKEN = os.getenv("TWITTER_AUTH_TOKEN", "").strip()
TWITTER_CT0 = os.getenv("TWITTER_CT0", "").strip()

# Legacy fixed pool name from the single-account era. Kept only so clear-all can
# still find and delete a row created by an older build.
COOKIE_ACCOUNT_NAME = "cookie_session"

# Prefix marking a pool row as a cookie (/auth) account, versus an interactive
# (/login) account stored under its real handle.
COOKIE_ACCOUNT_PREFIX = "cookie_"

# Persistent multi-account store. Sits next to .env; survives an accounts.db wipe
# and lets restore rebuild the whole pool on boot.
SESSIONS_FILE = Path(
    os.getenv("WHALE_SESSIONS_FILE", str(env_path.parent / ".sessions.json"))
)

# Serializes read-modify-write on SESSIONS_FILE across concurrent /auth commands.
_sessions_lock = asyncio.Lock()


def cookie_account_name(auth_token: str) -> str:
    """Deterministic pool row name for an auth_token.

    Same token -> same name, so re-running /auth updates that row's ct0 instead
    of creating a duplicate; a different token -> a new row, so the pool grows.
    """
    digest = hashlib.sha1(auth_token.strip().encode("utf-8")).hexdigest()[:10]
    return f"{COOKIE_ACCOUNT_PREFIX}{digest}"


def _read_sessions() -> List[Dict[str, str]]:
    """Load stored cookie sessions, tolerating a missing or corrupt file."""
    if not SESSIONS_FILE.exists():
        return []
    try:
        data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Sessions file unreadable (%s); treating as empty", e)
        return []
    if not isinstance(data, list):
        logger.warning("Sessions file is not a list; treating as empty")
        return []

    sessions: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        auth_token = str(item.get("auth_token", "")).strip()
        ct0 = str(item.get("ct0", "")).strip()
        if not auth_token or not ct0:
            continue
        username = item.get("username")
        sessions.append({
            "username": str(username).strip() if username else "",
            "auth_token": auth_token,
            "ct0": ct0,
        })
    return sessions


def _write_sessions(sessions: List[Dict[str, str]]) -> None:
    """Persist the session list and lock the file down to owner-only."""
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
    try:
        SESSIONS_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        # chmod is a near no-op on Windows (the dev host); 0600 matters on the VPS.
        pass


def _upsert_session_record(username: str, auth_token: str, ct0: str) -> None:
    """Add or refresh one session, keyed by auth_token."""
    at, c = auth_token.strip(), ct0.strip()
    sessions = _read_sessions()
    for record in sessions:
        if record["auth_token"] == at:
            record["ct0"] = c
            if username:
                record["username"] = username
            break
    else:
        sessions.append({"username": username or "", "auth_token": at, "ct0": c})
    _write_sessions(sessions)


def _clear_session_records() -> None:
    _write_sessions([])


def _write_env_var(key: str, value: str) -> None:
    """Persist a single KEY=value pair into the .env file."""
    line = f"{key}={value}"
    if not env_path.exists():
        env_path.write_text(line + "\n", encoding="utf-8")
        return

    content = env_path.read_text(encoding="utf-8")
    if re.search(rf'^{key}=.*$', content, flags=re.MULTILINE):
        new_content = re.sub(rf'^{key}=.*$', line, content, flags=re.MULTILINE)
    else:
        new_content = content.rstrip() + f"\n{line}\n"
    env_path.write_text(new_content, encoding="utf-8")


def update_twitter_auth_token(token: str, ct0: str = "") -> None:
    """Store the Twitter cookie pair in memory and in the .env file.

    This only records the credentials. Registering them with the twscrape pool
    is done separately by `register_cookie_account`, which must be awaited.
    """
    global TWITTER_AUTH_TOKEN, TWITTER_CT0
    TWITTER_AUTH_TOKEN = token.strip()
    TWITTER_CT0 = ct0.strip()
    os.environ["TWITTER_AUTH_TOKEN"] = TWITTER_AUTH_TOKEN
    os.environ["TWITTER_CT0"] = TWITTER_CT0
    _write_env_var("TWITTER_AUTH_TOKEN", TWITTER_AUTH_TOKEN)
    _write_env_var("TWITTER_CT0", TWITTER_CT0)


async def register_cookie_account(auth_token: str, ct0: str) -> str:
    """Register an auth_token/ct0 cookie pair as an active twscrape account.

    Both cookies are required: X rejects GraphQL calls whose x-csrf-token (ct0)
    does not match the session, so a placeholder ct0 yields a silently dead
    account that fails every request.

    The row is named deterministically from the token so re-registering the same
    token refreshes it in place while a new token adds another account. Returns
    the pool row name. Persistence-free by contract: `add_cookie_session` layers
    the sessions-file write on top, and the boot restore calls this directly.
    """
    from twscrape import AccountsPool

    pool = AccountsPool()
    name = cookie_account_name(auth_token)
    cookies = json.dumps({"auth_token": auth_token.strip(), "ct0": ct0.strip()})

    if not await pool.get_account(name):
        # twscrape reads password "_" as "cookie account", so it never attempts
        # a password login for this row.
        await pool.add_account(name, "_", f"{name}@local", "")
    await pool.add_account_cookies(name, cookies)
    await pool.set_active(name, True)
    return name


async def add_cookie_session(username: str, auth_token: str, ct0: str) -> str:
    """Register a cookie session in the pool AND persist it (the /auth path).

    `username` is the resolved X handle, stored as metadata so /auth_status can
    show real handles. The lock makes concurrent /auth commands safe.
    """
    async with _sessions_lock:
        name = await register_cookie_account(auth_token, ct0)
        _upsert_session_record(username, auth_token, ct0)
        return name


async def restore_cookie_accounts() -> int:
    """Re-register every stored cookie session on boot.

    Merges the sessions file with the legacy TWITTER_AUTH_TOKEN/TWITTER_CT0 env
    pair (deduped by auth_token) so older single-account deploys keep working and
    are migrated into the store. Returns the number registered.
    """
    sessions = _read_sessions()
    seen = {s["auth_token"] for s in sessions}

    legacy_at = os.getenv("TWITTER_AUTH_TOKEN", "").strip()
    legacy_ct0 = os.getenv("TWITTER_CT0", "").strip()
    if legacy_at and legacy_ct0 and legacy_at not in seen:
        sessions.append({"username": "", "auth_token": legacy_at, "ct0": legacy_ct0})
        try:
            _upsert_session_record("", legacy_at, legacy_ct0)  # one-time migrate
        except OSError:
            pass

    restored = 0
    for record in sessions:
        try:
            await register_cookie_account(record["auth_token"], record["ct0"])
            restored += 1
        except Exception as e:
            logger.warning(
                "Could not restore cookie session %s...: %s",
                record["auth_token"][:6], e,
            )
    if restored:
        logger.info("Restored %d Twitter cookie session(s)", restored)
    return restored


async def restore_cookie_account_from_env() -> bool:
    """Re-register the stored cookie session from environment variables.

    Hosts with an ephemeral filesystem (Render's free tier among them) wipe
    accounts.db on every restart, redeploy and spin-down, taking the X session
    with it. Re-registering from TWITTER_AUTH_TOKEN/TWITTER_CT0 at startup
    means a wiped database heals itself instead of needing /auth by hand after
    every wake-up.
    """
    auth_token = os.getenv("TWITTER_AUTH_TOKEN", "").strip()
    ct0 = os.getenv("TWITTER_CT0", "").strip()
    if not auth_token or not ct0:
        return False

    try:
        await register_cookie_account(auth_token, ct0)
    except Exception as e:
        logger.warning(f"Could not restore cookie session from environment: {e}")
        return False

    logger.info("Restored Twitter cookie session from environment")
    return True


async def clear_all_cookie_sessions() -> None:
    """Clear every cookie session: sessions file, legacy env, and pool rows.

    Blanks the legacy env pair too, so a boot restore cannot revive a session
    the user just cleared.
    """
    global TWITTER_AUTH_TOKEN, TWITTER_CT0
    TWITTER_AUTH_TOKEN = ""
    TWITTER_CT0 = ""
    os.environ["TWITTER_AUTH_TOKEN"] = ""
    os.environ["TWITTER_CT0"] = ""
    _write_env_var("TWITTER_AUTH_TOKEN", "")
    _write_env_var("TWITTER_CT0", "")

    async with _sessions_lock:
        _clear_session_records()

    try:
        from twscrape import AccountsPool
        pool = AccountsPool()
        accounts = await pool.get_all()
        # Remove every cookie row (current hash-named and any legacy fixed name).
        cookie_rows = [
            a.username for a in accounts
            if a.username == COOKIE_ACCOUNT_NAME
            or a.username.startswith(COOKIE_ACCOUNT_PREFIX)
        ]
        if cookie_rows:
            await pool.delete_accounts(cookie_rows)
        # Deactivate any remaining interactively logged-in accounts so the bot
        # really is unauthenticated afterwards. Rows are kept so /login revives.
        for account in await pool.get_all():
            if account.active:
                await pool.set_active(account.username, False)
    except Exception as e:
        logger.warning(f"Could not clear twscrape pool: {e}")


# Backward-compatible alias for the pre-multi-account name still imported by bot.py.
clear_twitter_auth_token = clear_all_cookie_sessions

import os
import re
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables from .env file
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TWITTER_AUTH_TOKEN = os.getenv("TWITTER_AUTH_TOKEN", "").strip()
TWITTER_CT0 = os.getenv("TWITTER_CT0", "").strip()

# Local pool account name used for cookie-based (/auth) sessions.
COOKIE_ACCOUNT_NAME = "cookie_session"


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


async def register_cookie_account(auth_token: str, ct0: str) -> None:
    """Register an auth_token/ct0 cookie pair as an active twscrape account.

    Both cookies are required: X rejects GraphQL calls whose x-csrf-token (ct0)
    does not match the session, so a placeholder ct0 yields a silently dead
    account that fails every request.
    """
    from twscrape import AccountsPool

    pool = AccountsPool()
    cookies = json.dumps({"auth_token": auth_token.strip(), "ct0": ct0.strip()})

    if not await pool.get_account(COOKIE_ACCOUNT_NAME):
        # twscrape reads password "_" as "cookie account", so it never attempts
        # a password login for this row.
        await pool.add_account(COOKIE_ACCOUNT_NAME, "_", f"{COOKIE_ACCOUNT_NAME}@local", "")
    await pool.add_account_cookies(COOKIE_ACCOUNT_NAME, cookies)
    await pool.set_active(COOKIE_ACCOUNT_NAME, True)


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


async def clear_twitter_auth_token() -> None:
    """Clear stored cookies from memory, the .env file, and the twscrape pool."""
    global TWITTER_AUTH_TOKEN, TWITTER_CT0
    TWITTER_AUTH_TOKEN = ""
    TWITTER_CT0 = ""
    os.environ["TWITTER_AUTH_TOKEN"] = ""
    os.environ["TWITTER_CT0"] = ""
    _write_env_var("TWITTER_AUTH_TOKEN", "")
    _write_env_var("TWITTER_CT0", "")

    try:
        from twscrape import AccountsPool
        pool = AccountsPool()
        await pool.delete_accounts([COOKIE_ACCOUNT_NAME])
        # Deactivate any interactively logged-in accounts so the bot really is
        # unauthenticated afterwards. Rows are kept so /login can revive them.
        for account in await pool.get_all():
            if account.active:
                await pool.set_active(account.username, False)
    except Exception as e:
        logger.warning(f"Could not clear twscrape pool: {e}")

import asyncio
import logging
from datetime import datetime, timezone
from typing import Tuple, List, Dict, Optional, Any

import httpx
from twscrape import API, AccountsPool
from twscrape.account import TOKEN as X_BEARER_TOKEN
from twscrape.login import login as twscrape_login

logger = logging.getLogger(__name__)

# Endpoint that returns the authenticated user's own profile. Used to resolve a
# cookie session's real handle and to validate the cookies at add-time.
VERIFY_CREDENTIALS_URL = "https://api.twitter.com/1.1/account/verify_credentials.json"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Password login is the endpoint X protects most aggressively. These markers
# mean the request never reached the login flow, so no credential change helps.
BLOCK_MARKERS = (
    "cloudflare",
    "you have been blocked",
    "unable to access",
    "attention required",
)


class TwitterAuthService:
    """Manages one Telegram user's Twitter / X login sessions in their pool."""

    # A session check must not hang the /auth_status reply.
    PROBE_TIMEOUT_SECONDS = 20

    # Any well-known handle works; only the auth outcome is of interest.
    PROBE_HANDLE = "jack"

    def __init__(self, pool: AccountsPool = None, store=None, api: API = None):
        self.pool = pool or AccountsPool()
        self.store = store  # SessionStore for this user; used to back up /login
        self.api = api or API(self.pool, raise_when_no_account=True)

    async def login_account(
        self,
        username: str,
        password: str,
        email: str,
        email_password: str = ""
    ) -> Tuple[bool, str]:
        """Add and log into a Twitter account, leaving it active in the pool."""
        handle = username.strip().lstrip('@')
        try:
            # Drop any previous row first. A stale one may still be flagged
            # active while holding dead cookies, and pool.login_all() skips
            # active accounts - so it would never be re-authenticated.
            await self.pool.delete_accounts([handle])
            await self.pool.add_account(
                username=handle,
                password=password.strip(),
                email=email.strip(),
                email_password=email_password.strip()
            )
            account = await self.pool.get_account(handle)
            if not account:
                return False, "Could not store the account. Please try again."

            # Drive the login directly rather than via pool.login_all(), which
            # swallows the exception. X can 403 before any login step runs, so
            # account.error_msg stays empty and the real reason is lost.
            try:
                await twscrape_login(account)
            except Exception as e:
                return False, self._explain_failure(str(e))
            finally:
                await self.pool.save(account)

            if not account.active:
                return False, self._explain_failure(account.error_msg or "")

            # Back the login up with its own cookies: persist auth_token/ct0 so
            # the session survives a DB wipe and keeps working (cookie-backed)
            # even if the password login later dies. Best-effort - a persistence
            # failure must not fail an otherwise successful login.
            await self._persist_login_cookies(handle, account)

            return True, f"Successfully logged into Twitter as @{handle}"
        except Exception as e:
            logger.error(f"Twitter login error for {handle}: {e}")
            return False, self._explain_failure(str(e))

    async def _persist_login_cookies(self, handle: str, account) -> None:
        """Store a freshly logged-in account's cookies as a restorable session."""
        if self.store is None:
            return
        try:
            cookies = getattr(account, "cookies", None) or {}
            auth_token = cookies.get("auth_token", "")
            ct0 = cookies.get("ct0", "")
            if auth_token and ct0:
                await self.store.add_cookie_session(handle, auth_token, ct0)
        except Exception as e:
            logger.warning(f"Could not persist login cookies for {handle}: {e}")

    @staticmethod
    def _explain_failure(reason: str) -> str:
        """Turn a raw twscrape/X error into something the user can act on."""
        text = reason.lower()

        if any(marker in text for marker in BLOCK_MARKERS) or "403" in text:
            return (
                "X blocked the login request from this IP before it reached the "
                "login form. This is a network-level block, so the credentials "
                "are not the problem. Use /auth with your browser cookies "
                "instead, or set TWS_PROXY in .env and retry."
            )
        if "ip ban" in text or "ct0 not in cookies" in text:
            return (
                "X refused to issue a session for this IP. Use /auth with your "
                "browser cookies, or set TWS_PROXY in .env and retry."
            )
        if "wrong password" in text or "incorrect" in text or "denied" in text:
            return "X rejected those credentials. Check the username and password."
        if "confirmation" in text or "acid" in text or "otp" in text or "code" in text:
            return (
                "X asked for a verification code, which this flow cannot answer. "
                "Use /auth with your browser cookies instead."
            )
        if "suspend" in text or "locked" in text:
            return "That account is locked or suspended. Unlock it at x.com first."

        return reason.strip() or "Login did not complete and X gave no reason."

    async def resolve_credentials(self, auth_token: str, ct0: str) -> Tuple[str, Optional[str]]:
        """Classify a cookie pair and resolve its handle.

        Returns (outcome, handle):
          ("ok", handle)      - 200, cookies are good, handle resolved
          ("invalid", None)   - 401, cookies are genuinely bad or expired
          ("unverified", None)- 403 / 429 / network / non-JSON: the check could
                                not run (datacenter-IP block or rate limit), so
                                the cookies MIGHT be fine. Caller may add anyway.
        """
        auth_token, ct0 = auth_token.strip(), ct0.strip()
        headers = {
            "authorization": X_BEARER_TOKEN,
            "x-csrf-token": ct0,
            "cookie": f"auth_token={auth_token}; ct0={ct0}",
            "user-agent": _BROWSER_UA,
        }
        try:
            async with httpx.AsyncClient(timeout=self.PROBE_TIMEOUT_SECONDS) as client:
                resp = await client.get(VERIFY_CREDENTIALS_URL, headers=headers)
        except Exception as e:
            logger.warning(f"verify_credentials request failed: {e}")
            return "unverified", None

        if resp.status_code == 200:
            try:
                screen_name = resp.json().get("screen_name")
            except Exception:
                return "unverified", None
            if screen_name:
                return "ok", screen_name.strip()
            return "unverified", None
        if resp.status_code == 401:
            return "invalid", None
        # 403 (IP/Cloudflare block), 429 (rate limit), or anything else: unknown.
        logger.info("verify_credentials returned %s; treating as unverified", resp.status_code)
        return "unverified", None

    async def get_active_accounts_count(self) -> int:
        """Return the number of logged-in, usable accounts in the pool."""
        try:
            stats = await self.pool.stats()
            return int(stats.get("active", 0))
        except Exception as e:
            logger.warning(f"Could not read twscrape pool stats: {e}")
            return 0

    async def get_pool_counts(self) -> Tuple[int, int]:
        """Return (total, active) account rows.

        Derived from get_all() rather than stats() so it does not depend on
        twscrape's stat key names across versions.
        """
        try:
            accounts = await self.pool.get_all()
            return len(accounts), sum(1 for a in accounts if a.active)
        except Exception as e:
            logger.warning(f"Could not read twscrape pool: {e}")
            return 0, 0

    async def account_health(self) -> List[Dict[str, Any]]:
        """Per-account health for /auth_status.

        Returns {name, active, locked_until, error_msg}. locked_until is the
        furthest future lock across all queues (i.e. throttled until then), or
        None when the account is free.
        """
        try:
            accounts = await self.pool.get_all()
        except Exception as e:
            logger.warning(f"Could not read twscrape pool: {e}")
            return []

        now = datetime.now(timezone.utc)
        health: List[Dict[str, Any]] = []
        for account in accounts:
            locked_until = self._max_future_lock(getattr(account, "locks", None), now)
            health.append({
                "name": account.username,
                "active": bool(account.active),
                "locked_until": locked_until,
                "error_msg": getattr(account, "error_msg", None),
            })
        return health

    @staticmethod
    def _max_future_lock(locks: Optional[Dict[str, Any]], now: datetime) -> Optional[datetime]:
        """Furthest future lock timestamp across queues, or None if all past."""
        if not isinstance(locks, dict):
            return None
        latest: Optional[datetime] = None
        for value in locks.values():
            parsed = value
            if isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value)
                except ValueError:
                    continue
            if not isinstance(parsed, datetime):
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed > now and (latest is None or parsed > latest):
                latest = parsed
        return latest

    async def is_authenticated(self) -> bool:
        """True when at least one account is flagged usable in the pool."""
        return await self.get_active_accounts_count() > 0

    async def verify_session(self) -> bool:
        """Check the stored session actually works, not just that a row says so.

        A dead cookie stays flagged active until something tries to use it, so
        reading the pool alone would report CONNECTED for an expired login.
        This spends one cheap request to find out; twscrape deactivates the
        account itself if X rejects it, so the flag afterwards is the answer.
        """
        if not await self.is_authenticated():
            return False

        try:
            await asyncio.wait_for(
                self.api.user_by_login(self.PROBE_HANDLE),
                timeout=self.PROBE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("Session probe timed out")
        except Exception as e:
            logger.warning(f"Session probe failed: {e}")

        return await self.is_authenticated()

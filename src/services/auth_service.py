import asyncio
import logging
from typing import Tuple
from twscrape import API, AccountsPool
from twscrape.login import login as twscrape_login

logger = logging.getLogger(__name__)

# Password login is the endpoint X protects most aggressively. These markers
# mean the request never reached the login flow, so no credential change helps.
BLOCK_MARKERS = (
    "cloudflare",
    "you have been blocked",
    "unable to access",
    "attention required",
)


class TwitterAuthService:
    """Manages Twitter / X login sessions stored in the shared twscrape pool."""

    # A session check must not hang the /auth_status reply.
    PROBE_TIMEOUT_SECONDS = 20

    # Any well-known handle works; only the auth outcome is of interest.
    PROBE_HANDLE = "jack"

    def __init__(self, pool: AccountsPool = None, api: API = None):
        self.pool = pool or AccountsPool()
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

            return True, f"Successfully logged into Twitter as @{handle}"
        except Exception as e:
            logger.error(f"Twitter login error for {handle}: {e}")
            return False, self._explain_failure(str(e))

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

    async def get_active_accounts_count(self) -> int:
        """Return the number of logged-in, usable accounts in the pool."""
        try:
            stats = await self.pool.stats()
            return int(stats.get("active", 0))
        except Exception as e:
            logger.warning(f"Could not read twscrape pool stats: {e}")
            return 0

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

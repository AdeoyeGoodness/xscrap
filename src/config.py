import os
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

# Per-user data lives here: data/<uid>.db (twscrape pool) and
# data/<uid>.sessions.json (cookie backup). One directory, one pair per user, so
# no Telegram user can see or use another's X accounts.
DATA_DIR = Path(os.getenv("WHALE_DATA_DIR", str(env_path.parent / "data")))

# Prefix marking a pool row as a cookie account whose real handle is unknown
# (unverified add), versus one stored under its resolved @handle.
COOKIE_ACCOUNT_PREFIX = "cookie_"


def cookie_account_name(auth_token: str) -> str:
    """Deterministic fallback pool row name for a handle-less cookie session.

    Same token -> same name, so re-adding updates that row's ct0 instead of
    duplicating. Used only when the handle could not be resolved.
    """
    digest = hashlib.sha1(auth_token.strip().encode("utf-8")).hexdigest()[:10]
    return f"{COOKIE_ACCOUNT_PREFIX}{digest}"


def account_name_for(username: str, auth_token: str) -> str:
    """Pool row name for a session: the real handle when known, else the hash.

    Naming by handle means /login and /auth for the same account land on ONE row
    (no duplicate) and /auth_status shows @handle without a lookup.
    """
    handle = (username or "").strip().lstrip("@").lower()
    return handle or cookie_account_name(auth_token)


def store_for_user(user_id: int) -> "SessionStore":
    """The private session store for one Telegram user."""
    return SessionStore(
        db_path=DATA_DIR / f"{user_id}.db",
        sessions_path=DATA_DIR / f"{user_id}.sessions.json",
    )


class SessionStore:
    """One Telegram user's private X sessions: a twscrape DB + a cookie backup.

    Every pool operation is scoped to this user's own db file, so twscrape's
    account rotation, /auth_status, and /logout can only ever touch this user's
    accounts.
    """

    def __init__(self, db_path: Path, sessions_path: Path):
        self.db_path = Path(db_path)
        self.sessions_path = Path(sessions_path)
        self._lock = asyncio.Lock()

    def _pool(self):
        from twscrape import AccountsPool
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return AccountsPool(str(self.db_path))

    # --- sessions file (cookie backup) -------------------------------------

    def read_sessions(self) -> List[Dict[str, str]]:
        """Stored sessions, tolerating a missing or corrupt file."""
        if not self.sessions_path.exists():
            return []
        try:
            data = json.loads(self.sessions_path.read_text(encoding="utf-8"))
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

    def _write_sessions(self, sessions: List[Dict[str, str]]) -> None:
        self.sessions_path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions_path.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
        try:
            self.sessions_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            # chmod is a near no-op on Windows (the dev host); 0600 matters on the VPS.
            pass

    def _upsert_record(self, username: str, auth_token: str, ct0: str) -> None:
        at, c = auth_token.strip(), ct0.strip()
        sessions = self.read_sessions()
        for record in sessions:
            if record["auth_token"] == at:
                record["ct0"] = c
                if username:
                    record["username"] = username
                break
        else:
            sessions.append({"username": username or "", "auth_token": at, "ct0": c})
        self._write_sessions(sessions)

    def _clear_records(self) -> None:
        self._write_sessions([])

    # --- pool registration --------------------------------------------------

    async def _register_account(self, name: str, auth_token: str, ct0: str) -> str:
        """Register/refresh one cookie-backed row under an explicit name.

        Both cookies are required: X rejects GraphQL calls whose x-csrf-token
        (ct0) does not match the session. If the row already exists (e.g. a
        /login account under its handle) its password is left intact; only the
        cookies and active flag are refreshed.
        """
        pool = self._pool()
        cookies = json.dumps({"auth_token": auth_token.strip(), "ct0": ct0.strip()})
        if not await pool.get_account(name):
            # twscrape reads password "_" as "cookie account", so it never
            # attempts a password login for this row.
            await pool.add_account(name, "_", f"{name}@local", "")
        await pool.add_account_cookies(name, cookies)
        await pool.set_active(name, True)
        return name

    async def register_cookie_account(self, auth_token: str, ct0: str) -> str:
        """Register an auth_token/ct0 pair under the deterministic hash name."""
        return await self._register_account(
            cookie_account_name(auth_token), auth_token, ct0
        )

    async def add_cookie_session(self, username: str, auth_token: str, ct0: str) -> str:
        """Register a session under its handle AND persist it (the /auth path).

        Used by /auth (resolved handle) and a successful /login (login handle).
        The lock makes concurrent writers for this user safe.
        """
        async with self._lock:
            name = await self._register_account(
                account_name_for(username, auth_token), auth_token, ct0
            )
            self._upsert_record(username, auth_token, ct0)
            return name

    async def restore(self) -> int:
        """Re-register every stored session (self-heals a wiped DB). Returns count."""
        restored = 0
        for record in self.read_sessions():
            try:
                name = account_name_for(record.get("username", ""), record["auth_token"])
                await self._register_account(name, record["auth_token"], record["ct0"])
                restored += 1
            except Exception as e:
                logger.warning(
                    "Could not restore cookie session %s...: %s",
                    record["auth_token"][:6], e,
                )
        if restored:
            logger.info("Restored %d Twitter session(s) for %s", restored, self.db_path.stem)
        return restored

    async def clear_all(self) -> None:
        """Clear this user's sessions file and delete all of their pool rows."""
        async with self._lock:
            self._clear_records()
        try:
            pool = self._pool()
            names = [a.username for a in await pool.get_all()]
            if names:
                await pool.delete_accounts(names)
        except Exception as e:
            logger.warning(f"Could not clear pool for {self.db_path.stem}: {e}")

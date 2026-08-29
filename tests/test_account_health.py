"""Tests for resolve_screen_name, pool counts, and per-account health."""

from datetime import datetime, timedelta, timezone

import pytest

from src.services.auth_service import TwitterAuthService


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Minimal stand-in for httpx.AsyncClient used by resolve_screen_name."""
    _resp = None
    _raise = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        if type(self)._raise:
            raise type(self)._raise
        return type(self)._resp


@pytest.fixture
def patch_httpx(monkeypatch):
    def _set(resp=None, raise_exc=None):
        _FakeAsyncClient._resp = resp
        _FakeAsyncClient._raise = raise_exc
        monkeypatch.setattr(
            "src.services.auth_service.httpx.AsyncClient", _FakeAsyncClient
        )
    return _set


@pytest.mark.asyncio
async def test_resolve_returns_handle_on_200(patch_httpx):
    patch_httpx(resp=_Resp(200, {"screen_name": "levelsio"}))
    svc = TwitterAuthService(pool=object())
    assert await svc.resolve_screen_name("tok", "ct0") == "levelsio"


@pytest.mark.asyncio
async def test_resolve_returns_none_on_non_200(patch_httpx):
    patch_httpx(resp=_Resp(401, {}))
    svc = TwitterAuthService(pool=object())
    assert await svc.resolve_screen_name("tok", "ct0") is None


@pytest.mark.asyncio
async def test_resolve_returns_none_on_network_error(patch_httpx):
    patch_httpx(raise_exc=OSError("connection reset"))
    svc = TwitterAuthService(pool=object())
    assert await svc.resolve_screen_name("tok", "ct0") is None


@pytest.mark.asyncio
async def test_resolve_returns_none_when_payload_lacks_handle(patch_httpx):
    patch_httpx(resp=_Resp(200, {"id": 123}))
    svc = TwitterAuthService(pool=object())
    assert await svc.resolve_screen_name("tok", "ct0") is None


class _Acct:
    def __init__(self, username, active, locks=None, error_msg=None):
        self.username = username
        self.active = active
        self.locks = locks or {}
        self.error_msg = error_msg


class _Pool:
    def __init__(self, accounts):
        self._accounts = accounts

    async def get_all(self):
        return self._accounts


@pytest.mark.asyncio
async def test_pool_counts():
    pool = _Pool([_Acct("a", True), _Acct("b", False), _Acct("c", True)])
    svc = TwitterAuthService(pool=pool)
    assert await svc.get_pool_counts() == (3, 2)


@pytest.mark.asyncio
async def test_account_health_reports_active_throttled_expired():
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    pool = _Pool([
        _Acct("free", True, locks={}),
        _Acct("throttled", True, locks={"SearchTimeline": future}),
        _Acct("stale_lock", True, locks={"SearchTimeline": past}),
        _Acct("dead", False, error_msg="(32) Could not authenticate you"),
    ])
    svc = TwitterAuthService(pool=pool)
    health = {h["name"]: h for h in await svc.account_health()}

    assert health["free"]["locked_until"] is None
    assert health["throttled"]["locked_until"] is not None
    assert health["stale_lock"]["locked_until"] is None   # lock already expired
    assert health["dead"]["active"] is False
    assert "authenticate" in health["dead"]["error_msg"]


def test_max_future_lock_picks_furthest():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    locks = {
        "A": (now + timedelta(minutes=5)).isoformat(),
        "B": (now + timedelta(minutes=40)).isoformat(),
        "C": (now - timedelta(minutes=99)).isoformat(),  # past, ignored
    }
    latest = TwitterAuthService._max_future_lock(locks, now)
    assert latest == now + timedelta(minutes=40)


def test_max_future_lock_none_when_all_past_or_empty():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert TwitterAuthService._max_future_lock({}, now) is None
    assert TwitterAuthService._max_future_lock(None, now) is None
    assert TwitterAuthService._max_future_lock(
        {"A": (now - timedelta(minutes=1)).isoformat()}, now
    ) is None

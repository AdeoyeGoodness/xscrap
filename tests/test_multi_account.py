"""Tests for the per-user SessionStore in src/config.py."""

import json

import pytest

from src import config
from src.config import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(
        db_path=tmp_path / "42.db",
        sessions_path=tmp_path / "42.sessions.json",
    )


@pytest.fixture
def no_pool(monkeypatch):
    """Record _register_account calls without touching a real twscrape pool."""
    calls = []

    async def fake_register(self, name, auth_token, ct0):
        calls.append((name, auth_token, ct0))
        return name

    monkeypatch.setattr(SessionStore, "_register_account", fake_register)
    return calls


def test_cookie_account_name_is_deterministic_and_prefixed():
    a = config.cookie_account_name("tokenAAA")
    assert a == config.cookie_account_name("  tokenAAA  ")
    assert a.startswith("cookie_")
    assert a != config.cookie_account_name("tokenBBB")


def test_account_name_prefers_handle_falls_back_to_hash():
    assert config.account_name_for("Alice", "tokA") == "alice"
    assert config.account_name_for("@Bob", "tokB") == "bob"
    assert config.account_name_for("", "tokC") == config.cookie_account_name("tokC")


def test_read_sessions_missing_file_is_empty(store):
    assert store.read_sessions() == []


def test_read_sessions_corrupt_file_is_empty(store):
    store.sessions_path.write_text("{ not json", encoding="utf-8")
    assert store.read_sessions() == []


def test_read_sessions_non_list_is_empty(store):
    store.sessions_path.write_text('{"username": "x"}', encoding="utf-8")
    assert store.read_sessions() == []


def test_read_sessions_skips_records_missing_cookies(store):
    store.sessions_path.write_text(json.dumps([
        {"username": "a", "auth_token": "t1", "ct0": "c1"},
        {"username": "b", "auth_token": "t2"},   # no ct0
        {"username": "c", "ct0": "c3"},           # no auth_token
    ]), encoding="utf-8")
    assert [s["auth_token"] for s in store.read_sessions()] == ["t1"]


def test_upsert_appends_then_updates_in_place(store):
    store._upsert_record("alice", "tokA", "ct0A")
    store._upsert_record("bob", "tokB", "ct0B")
    store._upsert_record("alice", "tokA", "ct0A_NEW")

    got = store.read_sessions()
    assert len(got) == 2
    alice = next(s for s in got if s["auth_token"] == "tokA")
    assert alice["ct0"] == "ct0A_NEW"


@pytest.mark.asyncio
async def test_add_cookie_session_registers_under_handle_and_persists(store, no_pool):
    name = await store.add_cookie_session("alice", "tokA", "ct0A")

    assert name == "alice"  # handle, not hash
    assert no_pool == [("alice", "tokA", "ct0A")]
    assert store.read_sessions() == [
        {"username": "alice", "auth_token": "tokA", "ct0": "ct0A"}
    ]


@pytest.mark.asyncio
async def test_unverified_add_uses_hash_name(store, no_pool):
    # An unverified add passes username="" -> hash-named row.
    name = await store.add_cookie_session("", "tokX", "ct0X")
    assert name == config.cookie_account_name("tokX")


@pytest.mark.asyncio
async def test_second_auth_accumulates(store, no_pool):
    await store.add_cookie_session("alice", "tokA", "ct0A")
    await store.add_cookie_session("bob", "tokB", "ct0B")

    assert [s["username"] for s in store.read_sessions()] == ["alice", "bob"]
    assert len(no_pool) == 2


@pytest.mark.asyncio
async def test_reauth_same_account_updates_not_duplicates(store, no_pool):
    await store.add_cookie_session("alice", "tokA", "ct0_old")
    await store.add_cookie_session("alice", "tokA", "ct0_new")

    got = store.read_sessions()
    assert len(got) == 1
    assert got[0]["ct0"] == "ct0_new"


@pytest.mark.asyncio
async def test_restore_registers_every_stored_session(store, no_pool):
    store._upsert_record("alice", "tokA", "ct0A")
    store._upsert_record("bob", "tokB", "ct0B")

    count = await store.restore()

    assert count == 2
    assert {(n, a, c) for (n, a, c) in no_pool} == {
        ("alice", "tokA", "ct0A"), ("bob", "tokB", "ct0B")
    }


@pytest.mark.asyncio
async def test_login_persists_cookies_via_store(store, monkeypatch):
    """A successful /login backs itself up in the user's store for restore."""
    from src.services.auth_service import TwitterAuthService

    captured = []

    async def fake_register(self, name, auth_token, ct0):
        captured.append((name, auth_token, ct0))
        return name

    monkeypatch.setattr(SessionStore, "_register_account", fake_register)

    class _Acct:
        active = True
        error_msg = None
        cookies = {"auth_token": "loginTok", "ct0": "loginCt0"}

    class _Pool:
        async def delete_accounts(self, names): pass
        async def add_account(self, *a, **k): pass
        async def get_account(self, name): return _Acct()
        async def save(self, acc): pass

    monkeypatch.setattr(
        "src.services.auth_service.twscrape_login",
        lambda account: _awaitable(None),
    )

    svc = TwitterAuthService(pool=_Pool(), store=store, api=object())
    ok, _ = await svc.login_account("CyberSwag", "pw", "e@mail")

    assert ok is True
    assert ("cyberswag", "loginTok", "loginCt0") in captured
    assert store.read_sessions() == [
        {"username": "CyberSwag", "auth_token": "loginTok", "ct0": "loginCt0"}
    ]


def _awaitable(value):
    async def _coro():
        return value
    return _coro()

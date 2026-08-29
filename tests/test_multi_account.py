"""Tests for the multi-account cookie store in src/config.py."""

import json

import pytest

from src import config


@pytest.fixture
def sessions_file(tmp_path, monkeypatch):
    """Point the store at a temp file for the duration of a test."""
    path = tmp_path / ".sessions.json"
    monkeypatch.setattr(config, "SESSIONS_FILE", path)
    return path


@pytest.fixture
def captured_registrations(monkeypatch):
    """Record register_cookie_account calls without touching a real pool."""
    calls = []

    async def fake_register(auth_token, ct0):
        calls.append((auth_token, ct0))
        return config.cookie_account_name(auth_token)

    monkeypatch.setattr(config, "register_cookie_account", fake_register)
    return calls


def test_cookie_account_name_is_deterministic_and_prefixed():
    a = config.cookie_account_name("tokenAAA")
    assert a == config.cookie_account_name("  tokenAAA  ")  # trimmed
    assert a.startswith("cookie_")
    assert a != config.cookie_account_name("tokenBBB")


def test_read_sessions_missing_file_is_empty(sessions_file):
    assert config._read_sessions() == []


def test_read_sessions_corrupt_file_is_empty(sessions_file):
    sessions_file.write_text("{ this is not json", encoding="utf-8")
    assert config._read_sessions() == []


def test_read_sessions_non_list_is_empty(sessions_file):
    sessions_file.write_text('{"username": "x"}', encoding="utf-8")
    assert config._read_sessions() == []


def test_read_sessions_skips_records_missing_cookies(sessions_file):
    sessions_file.write_text(json.dumps([
        {"username": "a", "auth_token": "t1", "ct0": "c1"},
        {"username": "b", "auth_token": "t2"},          # no ct0 -> skipped
        {"username": "c", "ct0": "c3"},                  # no auth_token -> skipped
    ]), encoding="utf-8")
    got = config._read_sessions()
    assert [s["auth_token"] for s in got] == ["t1"]


def test_upsert_appends_then_updates_in_place(sessions_file):
    config._upsert_session_record("alice", "tokA", "ct0A")
    config._upsert_session_record("bob", "tokB", "ct0B")
    config._upsert_session_record("alice", "tokA", "ct0A_NEW")  # same token

    got = config._read_sessions()
    assert len(got) == 2
    alice = next(s for s in got if s["auth_token"] == "tokA")
    assert alice["ct0"] == "ct0A_NEW"
    assert alice["username"] == "alice"


def test_write_sessions_round_trips(sessions_file):
    config._upsert_session_record("h", "t", "c")
    assert config._read_sessions() == [
        {"username": "h", "auth_token": "t", "ct0": "c"}
    ]


@pytest.mark.asyncio
async def test_add_cookie_session_registers_and_persists(sessions_file, captured_registrations):
    name = await config.add_cookie_session("alice", "tokA", "ct0A")

    assert name == config.cookie_account_name("tokA")
    assert captured_registrations == [("tokA", "ct0A")]
    stored = config._read_sessions()
    assert stored == [{"username": "alice", "auth_token": "tokA", "ct0": "ct0A"}]


@pytest.mark.asyncio
async def test_second_auth_accumulates(sessions_file, captured_registrations):
    await config.add_cookie_session("alice", "tokA", "ct0A")
    await config.add_cookie_session("bob", "tokB", "ct0B")

    stored = config._read_sessions()
    assert [s["username"] for s in stored] == ["alice", "bob"]
    assert len(captured_registrations) == 2


@pytest.mark.asyncio
async def test_reauth_same_account_updates_not_duplicates(sessions_file, captured_registrations):
    await config.add_cookie_session("alice", "tokA", "ct0_old")
    await config.add_cookie_session("alice", "tokA", "ct0_new")

    stored = config._read_sessions()
    assert len(stored) == 1
    assert stored[0]["ct0"] == "ct0_new"


@pytest.mark.asyncio
async def test_restore_registers_every_stored_session(sessions_file, captured_registrations, monkeypatch):
    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_CT0", raising=False)
    config._upsert_session_record("alice", "tokA", "ct0A")
    config._upsert_session_record("bob", "tokB", "ct0B")

    count = await config.restore_cookie_accounts()

    assert count == 2
    assert set(captured_registrations) == {("tokA", "ct0A"), ("tokB", "ct0B")}


@pytest.mark.asyncio
async def test_restore_merges_legacy_env_pair(sessions_file, captured_registrations, monkeypatch):
    config._upsert_session_record("alice", "tokA", "ct0A")
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "legacyTok")
    monkeypatch.setenv("TWITTER_CT0", "legacyCt0")

    count = await config.restore_cookie_accounts()

    assert count == 2
    assert ("legacyTok", "legacyCt0") in captured_registrations
    # And it was migrated into the store so it is no longer env-only.
    assert any(s["auth_token"] == "legacyTok" for s in config._read_sessions())


@pytest.mark.asyncio
async def test_restore_does_not_double_count_env_already_stored(sessions_file, captured_registrations, monkeypatch):
    config._upsert_session_record("alice", "tokA", "ct0A")
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "tokA")   # same token as stored
    monkeypatch.setenv("TWITTER_CT0", "ct0A")

    count = await config.restore_cookie_accounts()

    assert count == 1
    assert captured_registrations == [("tokA", "ct0A")]

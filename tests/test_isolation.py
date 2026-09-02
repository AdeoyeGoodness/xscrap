"""Proves two users' SessionStores never see each other's sessions."""

import pytest

from src.config import SessionStore, store_for_user


@pytest.fixture
def no_pool(monkeypatch):
    async def fake_register(self, name, auth_token, ct0):
        return name
    monkeypatch.setattr(SessionStore, "_register_account", fake_register)


def test_store_for_user_gives_distinct_paths_per_user():
    a = store_for_user(111)
    b = store_for_user(222)
    assert a.db_path != b.db_path
    assert a.sessions_path != b.sessions_path
    assert a.db_path.name == "111.db"
    assert b.db_path.name == "222.db"


@pytest.mark.asyncio
async def test_one_users_add_does_not_appear_in_anothers_store(tmp_path, no_pool):
    alice = SessionStore(tmp_path / "1.db", tmp_path / "1.sessions.json")
    bob = SessionStore(tmp_path / "2.db", tmp_path / "2.sessions.json")

    await alice.add_cookie_session("alice", "tokA", "ct0A")

    assert [s["username"] for s in alice.read_sessions()] == ["alice"]
    assert bob.read_sessions() == []  # bob sees nothing of alice's


@pytest.mark.asyncio
async def test_clearing_one_store_leaves_the_other_intact(tmp_path, no_pool, monkeypatch):
    # clear_all also touches the pool; stub that out to a no-op here.
    # _pool is a SYNC method returning a pool object.
    def fake_pool(self):
        class _P:
            async def get_all(self): return []
            async def delete_accounts(self, names): pass
        return _P()
    monkeypatch.setattr(SessionStore, "_pool", fake_pool)

    alice = SessionStore(tmp_path / "1.db", tmp_path / "1.sessions.json")
    bob = SessionStore(tmp_path / "2.db", tmp_path / "2.sessions.json")
    await alice.add_cookie_session("alice", "tokA", "ct0A")
    await bob.add_cookie_session("bob", "tokB", "ct0B")

    await alice.clear_all()

    assert alice.read_sessions() == []
    assert [s["username"] for s in bob.read_sessions()] == ["bob"]  # untouched

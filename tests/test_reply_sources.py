"""Tests for the two-source commenter fetch.

These drive the real fetch_commenter_usernames (not the conftest stub) against
a fake twscrape API, to prove the union/dedup of the direct-reply and
conversation-search sources.
"""

import pytest

from src.extractors.twitter import TwitterExtractor


class _User:
    def __init__(self, username):
        self.username = username


class _Tweet:
    def __init__(self, username):
        self.user = _User(username)


class _Pool:
    def __init__(self, active=1):
        self._active = active

    async def stats(self):
        return {"active": self._active}


class FakeApi:
    """Serves scripted results for tweet_replies and search.

    Each is an async generator, matching what twscrape.gather consumes.
    `raise_on` makes one source blow up so we can prove the other survives.
    """

    def __init__(self, direct=(), conversation=(), pool=None, raise_on=None):
        self.direct = list(direct)
        self.conversation = list(conversation)
        self.pool = pool or _Pool()
        self.raise_on = raise_on
        self.search_queries = []

    async def tweet_replies(self, twid, limit=-1, kv=None):
        if self.raise_on == "direct":
            raise RuntimeError("boom")
        for username in self.direct:
            yield _Tweet(username)

    async def search(self, q, limit=-1, kv=None):
        self.search_queries.append(q)
        if self.raise_on == "search":
            raise RuntimeError("boom")
        for username in self.conversation:
            yield _Tweet(username)


def make_extractor(**kwargs):
    return TwitterExtractor(api=FakeApi(**kwargs))


@pytest.mark.asyncio
async def test_union_of_both_sources_deduplicated():
    """Nested repliers found only by search must be included."""
    ext = make_extractor(
        direct=["alice", "bob"],
        conversation=["bob", "carol", "dave"],  # bob overlaps; carol/dave are nested
    )
    result = await ext.fetch_commenter_usernames("20")

    assert result.usernames == ["alice", "bob", "carol", "dave"]
    assert result.session_expired is False


@pytest.mark.asyncio
async def test_dedup_is_case_insensitive():
    ext = make_extractor(direct=["Alice"], conversation=["alice", "ALICE"])
    result = await ext.fetch_commenter_usernames("20")

    assert result.usernames == ["Alice"]


@pytest.mark.asyncio
async def test_search_uses_conversation_id_operator():
    ext = make_extractor(direct=["alice"], conversation=[])
    await ext.fetch_commenter_usernames("2093533726709850513")

    assert ext.tw_api.search_queries == ["conversation_id:2093533726709850513"]


@pytest.mark.asyncio
async def test_search_failure_still_returns_direct_replies():
    """One dead source must not sink the whole fetch."""
    ext = make_extractor(direct=["alice", "bob"], raise_on="search")
    result = await ext.fetch_commenter_usernames("20")

    assert result.usernames == ["alice", "bob"]
    assert result.session_expired is False


@pytest.mark.asyncio
async def test_direct_failure_still_returns_search_results():
    ext = make_extractor(conversation=["carol"], raise_on="direct")
    result = await ext.fetch_commenter_usernames("20")

    assert result.usernames == ["carol"]


@pytest.mark.asyncio
async def test_no_active_account_skips_both_sources():
    ext = make_extractor(direct=["alice"], conversation=["bob"], pool=_Pool(active=0))
    result = await ext.fetch_commenter_usernames("20")

    assert result.usernames == []
    # Search must not have run at all.
    assert ext.tw_api.search_queries == []


@pytest.mark.asyncio
async def test_session_dying_mid_fetch_is_flagged():
    """Active before the fetch, gone after -> expired, not merely empty."""
    pool = _Pool(active=1)
    ext = make_extractor(direct=["alice"], conversation=["bob"], pool=pool)

    original_search = ext.tw_api.search

    async def search_then_die(q, limit=-1, kv=None):
        async for tweet in original_search(q, limit=limit, kv=kv):
            yield tweet
        pool._active = 0  # X rejected the session by the end of the fetch

    ext.tw_api.search = search_then_die
    result = await ext.fetch_commenter_usernames("20")

    assert result.session_expired is True
    assert result.usernames == ["alice", "bob"]

import pytest


def test_extract_twitter_standard_url(twitter_extractor):
    assert twitter_extractor.extract_username("https://twitter.com/elonmusk") == "elonmusk"
    assert twitter_extractor.extract_username("https://x.com/jack") == "jack"


def test_extract_mobile_url(twitter_extractor):
    assert twitter_extractor.extract_username("https://mobile.twitter.com/sama") == "sama"


def test_extract_status_url(twitter_extractor):
    assert twitter_extractor.extract_username("https://x.com/naval/status/1234567890") == "naval"


def test_extract_intent_url(twitter_extractor):
    assert twitter_extractor.extract_username("https://twitter.com/intent/user?screen_name=paulg") == "paulg"


def test_extract_handle_with_at(twitter_extractor):
    assert twitter_extractor.extract_username("@levelsio") == "levelsio"


def test_extract_url_with_query_params(twitter_extractor):
    assert twitter_extractor.extract_username("https://x.com/alexhormozi?s=20&t=abcdef") == "alexhormozi"


def test_reserved_routes_ignored(twitter_extractor):
    assert twitter_extractor.extract_username("https://x.com/home") is None
    assert twitter_extractor.extract_username("https://twitter.com/explore") is None
    assert twitter_extractor.extract_username("https://x.com/settings") is None


def test_canonical_url(twitter_extractor):
    assert twitter_extractor.get_canonical_url("elonmusk") == "https://x.com/elonmusk"
    assert twitter_extractor.get_canonical_url("@jack") == "https://x.com/jack"


@pytest.mark.asyncio
async def test_enrichment_service_multiple_links(enrichment_service):
    message = "Check out these leads: https://x.com/elonmusk and https://twitter.com/sama!"
    leads = await enrichment_service.extract_leads_from_text(message)

    usernames = [l["username"] for l in leads]
    assert "elonmusk" in usernames
    assert "sama" in usernames


@pytest.mark.asyncio
async def test_extract_all_usernames_from_text_mentions(enrichment_service):
    message = "Hey @elonmusk check out @jack and @sama on https://x.com/naval/status/20!"
    leads = await enrichment_service.extract_leads_from_text(message)

    usernames = [l["username"] for l in leads]
    assert "naval" in usernames
    assert "jack" in usernames
    assert "elonmusk" in usernames
    assert "sama" in usernames


@pytest.mark.asyncio
async def test_leads_carry_username_only(make_service):
    """A lead is a username and its identifiers - no name, followers or bio."""
    service, _ = make_service(commenters={"20": ["alice"]}, authenticated=True)
    leads = await service.extract_leads_from_text("https://x.com/naval/status/20")

    assert leads
    for lead in leads:
        assert set(lead) == {
            "platform_name", "platform_code", "username", "canonical_url", "role"
        }


@pytest.mark.asyncio
async def test_authenticated_post_returns_author_and_commenters(make_service):
    service, extractor = make_service(
        commenters={"20": ["alice", "bob", "carol"]}, authenticated=True
    )
    leads = await service.extract_leads_from_text("https://x.com/naval/status/20")

    usernames = [l["username"] for l in leads]
    assert usernames == ["naval", "alice", "bob", "carol"]
    # The message is processed once, so replies are fetched exactly once.
    assert extractor.reply_calls == ["20"]


@pytest.mark.asyncio
async def test_unauthenticated_post_returns_author_only(make_service):
    service, _ = make_service(commenters={"20": ["alice", "bob"]}, authenticated=False)
    leads = await service.extract_leads_from_text("https://x.com/naval/status/20")

    assert [l["username"] for l in leads] == ["naval"]


@pytest.mark.asyncio
async def test_commenters_are_deduplicated_against_author(make_service):
    service, _ = make_service(
        commenters={"20": ["Naval", "alice", "alice"]}, authenticated=True
    )
    leads = await service.extract_leads_from_text("https://x.com/naval/status/20")

    assert [l["username"] for l in leads] == ["naval", "alice"]


@pytest.mark.asyncio
async def test_two_post_links_each_fetched_once(make_service):
    service, extractor = make_service(
        commenters={"20": ["alice"], "21": ["bob"]}, authenticated=True
    )
    message = "https://x.com/naval/status/20 and https://x.com/jack/status/21"
    leads = await service.extract_leads_from_text(message)

    assert [l["username"] for l in leads] == ["naval", "jack", "alice", "bob"]
    assert extractor.reply_calls == ["20", "21"]


@pytest.mark.asyncio
async def test_no_usernames_returns_empty(enrichment_service):
    assert await enrichment_service.extract_leads_from_text("just some plain text") == []

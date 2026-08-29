import pytest

POST = "https://x.com/naval/status/20"


@pytest.mark.asyncio
async def test_expired_session_is_flagged(make_service):
    service, _ = make_service(authenticated=True, expires_on_fetch=True)
    result = await service.extract(POST)

    assert result.session_expired is True
    # The author still comes back - the message is not lost.
    assert [l["username"] for l in result.leads] == ["naval"]


@pytest.mark.asyncio
async def test_post_with_no_replies_is_not_an_expired_session(make_service):
    """The distinction this whole path exists for."""
    service, _ = make_service(commenters={"20": []}, authenticated=True)
    result = await service.extract(POST)

    assert result.session_expired is False
    assert [l["username"] for l in result.leads] == ["naval"]


@pytest.mark.asyncio
async def test_never_authenticated_is_not_an_expired_session(make_service):
    """No session and a dead session are different states."""
    service, _ = make_service(commenters={"20": ["alice"]}, authenticated=False)
    result = await service.extract(POST)

    assert result.session_expired is False
    assert [l["username"] for l in result.leads] == ["naval"]


@pytest.mark.asyncio
async def test_healthy_session_is_not_flagged(make_service):
    service, _ = make_service(commenters={"20": ["alice"]}, authenticated=True)
    result = await service.extract(POST)

    assert result.session_expired is False
    assert [l["username"] for l in result.leads] == ["naval", "alice"]


@pytest.mark.asyncio
async def test_expiry_on_one_link_flags_the_whole_message(make_service):
    """Once the session dies, later links in the same message cannot recover it."""
    service, extractor = make_service(
        commenters={"20": ["alice"], "21": ["bob"]},
        authenticated=True,
        expires_on_fetch=True,
    )
    result = await service.extract(f"{POST} and https://x.com/jack/status/21")

    assert result.session_expired is True
    assert [l["username"] for l in result.leads] == ["naval", "jack"]
    assert extractor.reply_calls == ["20", "21"]


@pytest.mark.asyncio
async def test_legacy_helper_still_returns_a_plain_list(make_service):
    service, _ = make_service(commenters={"20": ["alice"]}, authenticated=True)
    leads = await service.extract_leads_from_text(POST)

    assert isinstance(leads, list)
    assert [l["username"] for l in leads] == ["naval", "alice"]

import pytest

from src import config


@pytest.fixture
def captured(monkeypatch):
    """Record register_cookie_account calls instead of touching the pool."""
    calls = []

    async def fake_register(auth_token, ct0):
        calls.append((auth_token, ct0))

    monkeypatch.setattr(config, "register_cookie_account", fake_register)
    return calls


@pytest.mark.asyncio
async def test_restores_when_both_cookies_are_present(monkeypatch, captured):
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWITTER_CT0", "csrf")

    assert await config.restore_cookie_account_from_env() is True
    assert captured == [("tok", "csrf")]


@pytest.mark.asyncio
async def test_auth_token_alone_is_not_enough(monkeypatch, captured):
    """A half-configured session must not register a broken account."""
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "tok")
    monkeypatch.delenv("TWITTER_CT0", raising=False)

    assert await config.restore_cookie_account_from_env() is False
    assert captured == []


@pytest.mark.asyncio
async def test_no_cookies_configured_is_not_an_error(monkeypatch, captured):
    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_CT0", raising=False)

    assert await config.restore_cookie_account_from_env() is False
    assert captured == []


@pytest.mark.asyncio
async def test_blank_values_are_treated_as_unset(monkeypatch, captured):
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "  ")
    monkeypatch.setenv("TWITTER_CT0", "  ")

    assert await config.restore_cookie_account_from_env() is False
    assert captured == []


@pytest.mark.asyncio
async def test_a_failing_pool_does_not_stop_the_bot_starting(monkeypatch):
    """Startup must survive an unusable pool rather than crash the process."""
    async def boom(auth_token, ct0):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(config, "register_cookie_account", boom)
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWITTER_CT0", "csrf")

    assert await config.restore_cookie_account_from_env() is False

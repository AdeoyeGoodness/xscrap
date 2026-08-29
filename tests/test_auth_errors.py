import pytest

from src.services.auth_service import TwitterAuthService

explain = TwitterAuthService._explain_failure


def test_cloudflare_block_points_at_cookie_auth():
    raw = "403 - <html><h1>Sorry, you have been blocked</h1> Cloudflare Ray ID: a320afde"
    message = explain(raw)

    assert "/auth" in message
    assert "TWS_PROXY" in message
    # The user must not be sent off to re-check working credentials.
    assert "password" not in message.lower()


def test_ip_ban_assertion_points_at_cookie_auth():
    message = explain("ct0 not in cookies (most likely ip ban)")
    assert "/auth" in message


def test_bad_credentials_says_so():
    message = explain("login_step=LoginEnterPassword err=wrong password")
    assert "credentials" in message.lower()
    assert "/auth" not in message


def test_verification_code_prompt_points_at_cookie_auth():
    message = explain("login_step=LoginAcid err=confirmation code required")
    assert "/auth" in message


def test_suspended_account_says_so():
    message = explain("account is suspended")
    assert "suspended" in message.lower()


def test_unknown_reason_is_passed_through():
    assert explain("some novel failure") == "some novel failure"


def test_empty_reason_still_reads_as_a_sentence():
    assert explain("") == "Login did not complete and X gave no reason."


class FakePool:
    def __init__(self, active):
        self.active = active

    async def stats(self):
        return {"active": self.active}


class FakeApi:
    """Stands in for twscrape's API; `kills_session` mimics X rejecting a
    dead cookie, which twscrape answers by deactivating the account."""

    def __init__(self, pool, kills_session=False, raises=None):
        self.pool = pool
        self.kills_session = kills_session
        self.raises = raises
        self.calls = 0

    async def user_by_login(self, login):
        self.calls += 1
        if self.raises:
            raise self.raises
        if self.kills_session:
            self.pool.active = 0
        return None


def build_service(active, kills_session=False, raises=None):
    pool = FakePool(active)
    return TwitterAuthService(pool=pool, api=FakeApi(pool, kills_session, raises))


@pytest.mark.asyncio
async def test_verify_session_true_for_a_live_session():
    assert await build_service(active=1).verify_session() is True


@pytest.mark.asyncio
async def test_verify_session_false_when_probe_kills_the_session():
    """A dead cookie reads as active until something uses it."""
    service = build_service(active=1, kills_session=True)
    assert await service.verify_session() is False


@pytest.mark.asyncio
async def test_verify_session_skips_the_probe_when_never_authenticated():
    service = build_service(active=0)
    assert await service.verify_session() is False
    assert service.api.calls == 0


@pytest.mark.asyncio
async def test_unrelated_probe_failure_does_not_report_expiry():
    """A network blip must not be reported to the user as a dead login."""
    service = build_service(active=1, raises=OSError("connection reset"))
    assert await service.verify_session() is True

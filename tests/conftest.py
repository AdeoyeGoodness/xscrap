import pytest

from src.extractors.twitter import ReplyFetch, TwitterExtractor
from src.services.enrichment import EnrichmentService


class StubTwitterExtractor(TwitterExtractor):
    """TwitterExtractor with the network layer replaced.

    Tests must never touch the real twscrape pool or x.com. `commenters` maps a
    status id to the usernames the fake session returns; `expires_on_fetch`
    simulates X killing the session mid-request, which is what twscrape signals
    by deactivating the account.
    """

    def __init__(self, commenters=None, authenticated=False, expires_on_fetch=False):
        # Skip TwitterExtractor.__init__ so no API()/pool is constructed.
        self.tw_api = None
        self.commenters = commenters or {}
        self.authenticated = authenticated
        self.expires_on_fetch = expires_on_fetch
        self.reply_calls = []

    async def active_account_count(self) -> int:
        return 1 if self.authenticated else 0

    async def fetch_commenter_usernames(self, status_id: str) -> ReplyFetch:
        self.reply_calls.append(str(status_id))
        if not await self.active_account_count():
            return ReplyFetch(usernames=[])
        if self.expires_on_fetch:
            # twscrape marks the account inactive, so later calls see no session.
            self.authenticated = False
            return ReplyFetch(usernames=[], session_expired=True)
        return ReplyFetch(usernames=list(self.commenters.get(str(status_id), [])))


@pytest.fixture
def twitter_extractor():
    """Offline extractor for the pure parsing tests."""
    return StubTwitterExtractor()


@pytest.fixture
def make_service():
    """Build an EnrichmentService around a stubbed Twitter extractor."""
    def _make(commenters=None, authenticated=False, expires_on_fetch=False):
        extractor = StubTwitterExtractor(
            commenters=commenters,
            authenticated=authenticated,
            expires_on_fetch=expires_on_fetch,
        )
        return EnrichmentService(extractors=[extractor]), extractor
    return _make


@pytest.fixture
def enrichment_service(make_service):
    service, _ = make_service()
    return service

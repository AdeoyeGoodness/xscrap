import re
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Union
from urllib.parse import urlparse, parse_qs
from twscrape import API, gather
from src.extractors.base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)


@dataclass
class ReplyFetch:
    """Outcome of one reply lookup."""

    usernames: list
    session_expired: bool = False


class TwitterExtractor(BaseExtractor):
    """Extractor for Twitter / X URLs, status posts and handles.

    Output is deliberately minimal: a username per lead, nothing else.
    """

    RESERVED_ROUTES = {
        "home", "explore", "notifications", "messages", "bookmarks",
        "lists", "profile", "settings", "search", "hashtag", "i",
        "intent", "tos", "privacy", "signup", "login", "logout",
        "account", "analytics", "developer", "api", "jobs", "about",
        "download", "help", "rules", "share", "status"
    }

    # Maximum tweets pulled per source, per post link. Large threads need a
    # high ceiling; the conversation search in particular can be deep.
    REPLY_LIMIT = 1000

    # Hard ceiling on a single reply lookup, so one dead session cannot hang
    # the bot. Two sources run, so the whole fetch can take up to twice this.
    REPLY_TIMEOUT_SECONDS = 60

    # Match twitter.com / x.com / mobile.twitter.com profile or status URLs.
    # The lookahead lets the handle end on trailing punctuation ("…/sama!").
    URL_PATTERN = re.compile(
        r'(?:https?://)?(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/([a-zA-Z0-9_]{1,50})(?![a-zA-Z0-9_])',
        re.IGNORECASE
    )

    # Match status ID from a Twitter / X URL
    STATUS_PATTERN = re.compile(
        r'(?:twitter\.com|x\.com)/[a-zA-Z0-9_]{1,50}/status/(\d+)',
        re.IGNORECASE
    )

    # Match standalone handle e.g. @username
    HANDLE_PATTERN = re.compile(r'^@?([a-zA-Z0-9_]{1,15})$')

    # Match @mentions inside a text body
    MENTION_PATTERN = re.compile(r'@([a-zA-Z0-9_]{1,15})')

    def __init__(self, api: Optional[API] = None):
        # raise_when_no_account keeps a request from blocking forever when
        # every account is locked out or the stored session is dead.
        self.tw_api = api or API(raise_when_no_account=True)

    @property
    def platform_name(self) -> str:
        return "Twitter / X"

    @property
    def platform_code(self) -> str:
        return "twitter"

    def can_handle(self, url_or_text: str) -> bool:
        text = url_or_text.strip()
        if 'twitter.com' in text.lower() or 'x.com' in text.lower():
            return True
        return bool(self.MENTION_PATTERN.search(text))

    def extract_username(self, url_or_text: str) -> Optional[str]:
        """Extract the single primary username from a link or handle."""
        text = url_or_text.strip().rstrip('!.,?:;)"\'')

        # Intent URLs e.g. https://twitter.com/intent/user?screen_name=username
        if 'intent/user' in text.lower() or 'intent/follow' in text.lower():
            screen_name = self._intent_screen_name(text)
            if screen_name:
                return screen_name

        match = self.URL_PATTERN.search(text)
        if match:
            candidate = match.group(1)
            if candidate.lower() not in self.RESERVED_ROUTES:
                return candidate

        if text.startswith('@'):
            handle_match = self.HANDLE_PATTERN.match(text)
            if handle_match:
                candidate = handle_match.group(1)
                if candidate.lower() not in self.RESERVED_ROUTES:
                    return candidate

        return None

    def _intent_screen_name(self, text: str) -> Optional[str]:
        """Pull screen_name out of an intent URL, if present."""
        parsed = urlparse(text if text.startswith('http') else f'https://{text}')
        screen_name = parse_qs(parsed.query).get('screen_name')
        if screen_name and screen_name[0]:
            candidate = screen_name[0].strip('@')
            if candidate.lower() not in self.RESERVED_ROUTES:
                return candidate
        return None

    def get_canonical_url(self, username: str) -> str:
        return f"https://x.com/{username.lstrip('@')}"

    async def active_account_count(self) -> int:
        """Number of accounts the pool considers usable right now."""
        try:
            stats = await self.tw_api.pool.stats()
            return int(stats.get("active", 0))
        except Exception as e:
            logger.warning(f"Could not read twscrape pool stats: {e}")
            return 0

    async def has_active_account(self) -> bool:
        """True when the twscrape pool holds a usable, logged-in account."""
        return await self.active_account_count() > 0

    async def fetch_commenter_usernames(self, status_id: str) -> ReplyFetch:
        """Return the usernames of everyone who replied to a post.

        Requires an active account; unauthenticated calls are skipped rather
        than fired and silently failed.
        """
        active_before = await self.active_account_count()
        if not active_before:
            logger.info("Skipping reply fetch for %s: no active account", status_id)
            return ReplyFetch(usernames=[])

        twid = int(status_id)

        # Two complementary sources. tweet_replies (the TweetDetail view)
        # returns only direct replies to the post and drops nested ones; the
        # conversation search catches replies-to-replies and usually reaches
        # deeper into a large thread. Their union is far closer to the real
        # commenter set than either alone.
        direct = await self._collect_usernames(
            "direct replies",
            status_id,
            self.tw_api.tweet_replies(twid, limit=self.REPLY_LIMIT),
        )
        conversation = await self._collect_usernames(
            "conversation search",
            status_id,
            self.tw_api.search(f"conversation_id:{twid}", limit=self.REPLY_LIMIT),
        )

        seen: set = set()
        usernames: List[str] = []
        for name in [*direct, *conversation]:
            key = name.lower()
            if key not in seen:
                seen.add(key)
                usernames.append(name)

        logger.info(
            "Status %s: %d direct + %d via search -> %d unique commenter(s)",
            status_id, len(direct), len(conversation), len(usernames),
        )

        # twscrape deactivates an account itself when X answers "(32) Could not
        # authenticate you", a bare 403, or "(326) Denied by access control".
        # Losing every active account mid-request is therefore a dead session,
        # not a post that happens to have no replies.
        if not await self.active_account_count():
            logger.warning("Twitter session expired while reading status %s", status_id)
            return ReplyFetch(usernames=usernames, session_expired=True)

        return ReplyFetch(usernames=usernames)

    async def _collect_usernames(self, label: str, status_id: str, source) -> List[str]:
        """Drain one tweet async-generator into a list of usernames.

        Failures degrade to an empty list so one dead source never sinks the
        whole fetch; the other source still contributes what it found.
        """
        usernames: List[str] = []
        try:
            tweets = await asyncio.wait_for(
                gather(source), timeout=self.REPLY_TIMEOUT_SECONDS
            )
            for tweet in tweets:
                user = getattr(tweet, "user", None)
                username = getattr(user, "username", None)
                if username:
                    usernames.append(username)
        except asyncio.TimeoutError:
            logger.warning("%s for status %s timed out", label, status_id)
        except Exception as e:
            logger.warning("%s for status %s failed: %s", label, status_id, e)
        return usernames

    async def extract_all(self, url_or_text: str) -> ExtractionResult:
        """Extract every Twitter username referenced by the given text.

        Handles the whole message in one pass: profile/status URLs, intent
        links, @mentions and - when authenticated and a post link is present -
        the usernames of that post's commenters.
        """
        text = url_or_text.strip()
        leads: List[Dict[str, Any]] = []
        seen = set()
        session_expired = False

        def add_lead(username: Optional[str], role: str) -> None:
            clean_user = (username or "").strip().lstrip('@')
            if not clean_user or clean_user.lower() in self.RESERVED_ROUTES:
                return
            if clean_user.lower() in seen:
                return
            seen.add(clean_user.lower())
            leads.append({
                "platform_name": self.platform_name,
                "platform_code": self.platform_code,
                "username": clean_user,
                "canonical_url": self.get_canonical_url(clean_user),
                "role": role,
            })

        # 1. Authors of every profile / status URL in the message.
        for match in self.URL_PATTERN.finditer(text):
            add_lead(match.group(1), "Post / Profile Author")

        # 2. Intent links carry the handle in the query string instead.
        for token in re.split(r'\s+', text):
            if 'intent/user' in token.lower() or 'intent/follow' in token.lower():
                add_lead(self._intent_screen_name(token), "Post / Profile Author")

        # 3. Every @mention written in the message body.
        for mention in self.MENTION_PATTERN.findall(text):
            add_lead(mention, "Mentioned Handle")

        # 4. Commenters on each post link (authenticated sessions only).
        for status_match in self.STATUS_PATTERN.finditer(text):
            fetched = await self.fetch_commenter_usernames(status_match.group(1))
            session_expired = session_expired or fetched.session_expired
            for username in fetched.usernames:
                add_lead(username, "Commenter")

        return ExtractionResult(leads=leads, session_expired=session_expired)

    async def enrich(self, url_or_text: str) -> Union[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """Lead payloads for the given text, or None when nothing was found."""
        result = await self.extract_all(url_or_text)
        return result.leads or None

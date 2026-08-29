from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Union


@dataclass
class ExtractionResult:
    """Leads found in a message, plus why anything was missing.

    `session_expired` distinguishes "the stored login died" from "this post has
    no replies" - both otherwise look like an empty commenter list.
    """

    leads: List[Dict[str, Any]] = field(default_factory=list)
    session_expired: bool = False


class BaseExtractor(ABC):
    """Abstract Base Class for all Social Media Link Extractors."""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Human-readable platform name (e.g. 'Twitter / X')."""
        pass

    @property
    @abstractmethod
    def platform_code(self) -> str:
        """Unique key for the platform (e.g. 'twitter')."""
        pass

    @abstractmethod
    def can_handle(self, url_or_text: str) -> bool:
        """Check if the given URL or text can be handled by this extractor."""
        pass

    @abstractmethod
    def extract_username(self, url_or_text: str) -> Optional[str]:
        """Extract the username from a link or text string."""
        pass

    @abstractmethod
    def get_canonical_url(self, username: str) -> str:
        """Return the standard profile URL for a given username."""
        pass

    async def enrich(self, url_or_text: str) -> Union[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extract username(s) and return lead info payload(s)."""
        username = self.extract_username(url_or_text)
        if not username:
            return None

        return {
            "platform_name": self.platform_name,
            "platform_code": self.platform_code,
            "username": username,
            "canonical_url": self.get_canonical_url(username),
            "role": "Profile / Author",
        }

    async def extract_all(self, url_or_text: str) -> ExtractionResult:
        """Extract every lead in a message, with status about what was missed.

        Extractors that can lose an authenticated session override this; the
        default just wraps `enrich`.
        """
        enriched = await self.enrich(url_or_text)
        if not enriched:
            return ExtractionResult()
        leads = enriched if isinstance(enriched, list) else [enriched]
        return ExtractionResult(leads=leads)

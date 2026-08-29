import logging
from typing import List, Dict, Any, Optional
from src.extractors.base import BaseExtractor, ExtractionResult
from src.extractors.twitter import TwitterExtractor

logger = logging.getLogger(__name__)


class EnrichmentService:
    """Extracts social media usernames from an incoming message."""

    def __init__(self, extractors: Optional[List[BaseExtractor]] = None):
        self.extractors: List[BaseExtractor] = extractors or [
            TwitterExtractor()
        ]

    def register_extractor(self, extractor: BaseExtractor) -> None:
        """Register a new extractor for additional social media platforms."""
        self.extractors.append(extractor)

    async def extract(self, text: str) -> ExtractionResult:
        """Return deduplicated leads for a message, plus extraction status.

        Each extractor sees the whole message exactly once. Feeding it the full
        text and then again token by token would re-run the expensive reply
        lookup for every post link in the message.
        """
        results: List[Dict[str, Any]] = []
        seen_keys = set()
        session_expired = False

        for extractor in self.extractors:
            if not extractor.can_handle(text):
                continue
            try:
                extracted = await extractor.extract_all(text)
            except Exception as e:
                logger.warning(f"{extractor.platform_code} enrichment failed: {e}")
                continue

            session_expired = session_expired or extracted.session_expired
            for item in extracted.leads:
                key = (item["platform_code"], item["username"].lower())
                if key not in seen_keys:
                    seen_keys.add(key)
                    results.append(item)

        return ExtractionResult(leads=results, session_expired=session_expired)

    async def extract_leads_from_text(self, text: str) -> List[Dict[str, Any]]:
        """Return one deduplicated lead per username found in the message."""
        return (await self.extract(text)).leads

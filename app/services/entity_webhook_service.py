"""
Entity Webhook Service - Handles entity event notifications.

This service publishes entity events to configured webhooks for external integration.
"""

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any

import aiohttp

from ..config import settings

logger = logging.getLogger(__name__)


class EntityEventType(str, Enum):
    """Types of entity events"""

    ENTITY_CREATED = "entity.created"
    ENTITY_UPDATED = "entity.updated"
    ENTITY_MERGED = "entity.merged"
    ENTITY_ARCHIVED = "entity.archived"
    ENTITY_RESTORED = "entity.restored"
    ENTITY_ENRICHED = "entity.enriched"
    BULK_OPERATION_COMPLETED = "bulk.operation.completed"


class EntityWebhookService:
    """Service for publishing entity events to webhooks"""

    def __init__(self):
        self.webhook_urls = self._get_webhook_urls()
        self.webhook_secret = getattr(settings, "ENTITY_WEBHOOK_SECRET", None)
        self.timeout = aiohttp.ClientTimeout(total=30)
        self.max_retries = 3
        self.retry_delay = 1  # seconds

    def _get_webhook_urls(self) -> list[str]:
        """Get configured webhook URLs from settings"""
        # This could come from settings or a database
        urls = getattr(settings, "ENTITY_WEBHOOK_URLS", [])
        if isinstance(urls, str):
            urls = [urls]
        return [url for url in urls if url]

    async def publish_event(
        self,
        event_type: EntityEventType,
        entity_id: str,
        entity_type: str,
        data: dict[str, Any],
        user: str | None = None,
    ):
        """Publish an entity event to all configured webhooks"""

        if not self.webhook_urls:
            logger.debug("No webhook URLs configured, skipping event publication")
            return

        event = self._create_event(event_type, entity_id, entity_type, data, user)

        # Publish to all webhooks concurrently
        tasks = [self._send_webhook(url, event) for url in self.webhook_urls]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any failures
        for url, result in zip(self.webhook_urls, results, strict=False):
            if isinstance(result, Exception):
                logger.error(f"Failed to send webhook to {url}: {result}")
            else:
                logger.info(f"Successfully sent {event_type} event to {url}")

    def _create_event(
        self,
        event_type: EntityEventType,
        entity_id: str,
        entity_type: str,
        data: dict[str, Any],
        user: str | None = None,
    ) -> dict[str, Any]:
        """Create a standardized event payload"""

        event = {
            "event_id": f"evt_{datetime.utcnow().timestamp()}_{entity_id}",
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "entity": {"id": entity_id, "type": entity_type},
            "data": data,
            "metadata": {"version": "1.0", "source": "entity_api"},
        }

        if user:
            event["metadata"]["user"] = user

        return event

    async def _send_webhook(self, url: str, event: dict[str, Any]) -> bool:
        """Send event to a single webhook URL with retries"""

        payload = json.dumps(event)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "EntityWebhookService/1.0",
        }

        # Add signature if secret is configured
        if self.webhook_secret:
            signature = self._generate_signature(payload)
            headers["X-Entity-Signature"] = signature

        # Retry logic
        for attempt in range(self.max_retries):
            try:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.post(
                        url, data=payload, headers=headers
                    ) as response:
                        if response.status >= 200 and response.status < 300:
                            return True

                        # Log non-success responses
                        logger.warning(
                            f"Webhook returned status {response.status} for {url}: "
                            f"{await response.text()}"
                        )

                        # Don't retry client errors
                        if 400 <= response.status < 500:
                            return False

            except TimeoutError:
                logger.error(f"Webhook timeout for {url} (attempt {attempt + 1})")
            except Exception as e:
                logger.error(f"Webhook error for {url} (attempt {attempt + 1}): {e}")

            # Wait before retry
            if attempt < self.max_retries - 1:
                await asyncio.sleep(self.retry_delay * (attempt + 1))

        return False

    def _generate_signature(self, payload: str) -> str:
        """Generate HMAC signature for webhook payload"""
        if not self.webhook_secret:
            return ""

        signature = hmac.new(
            self.webhook_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        return f"sha256={signature}"

    async def publish_bulk_event(
        self,
        operation: str,
        entity_ids: list[str],
        results: dict[str, Any],
        user: str | None = None,
    ):
        """Publish a bulk operation completion event"""

        data = {
            "operation": operation,
            "entity_count": len(entity_ids),
            "entity_ids": entity_ids[:100],  # Limit to prevent huge payloads
            "results": results,
        }

        await self.publish_event(
            EntityEventType.BULK_OPERATION_COMPLETED,
            f"bulk_{operation}",
            "bulk",
            data,
            user,
        )

    def add_webhook_url(self, url: str):
        """Add a webhook URL at runtime"""
        if url and url not in self.webhook_urls:
            self.webhook_urls.append(url)
            logger.info(f"Added webhook URL: {url}")

    def remove_webhook_url(self, url: str):
        """Remove a webhook URL at runtime"""
        if url in self.webhook_urls:
            self.webhook_urls.remove(url)
            logger.info(f"Removed webhook URL: {url}")

    def get_webhook_urls(self) -> list[str]:
        """Get current webhook URLs"""
        return self.webhook_urls.copy()


# Global webhook service instance
_webhook_service = None


def get_webhook_service() -> EntityWebhookService:
    """Get or create the global webhook service instance"""
    global _webhook_service
    if _webhook_service is None:
        _webhook_service = EntityWebhookService()
    return _webhook_service

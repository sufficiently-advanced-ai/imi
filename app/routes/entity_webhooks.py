"""Entity Webhook Management API routes"""


from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl

from ..services.entity_webhook_service import EntityEventType, get_webhook_service

router = APIRouter(prefix="/api/entities/webhooks", tags=["entity-webhooks"])


class WebhookURLRequest(BaseModel):
    """Request to add/remove webhook URL"""

    url: HttpUrl


class WebhookTestRequest(BaseModel):
    """Request to test webhook"""

    url: HttpUrl
    event_type: EntityEventType = EntityEventType.ENTITY_UPDATED


class WebhookListResponse(BaseModel):
    """Response with list of webhook URLs"""

    urls: list[str]
    total: int


@router.get("", response_model=WebhookListResponse)
async def list_webhooks():
    """List all configured webhook URLs"""
    webhook_service = get_webhook_service()
    urls = webhook_service.get_webhook_urls()

    return WebhookListResponse(urls=urls, total=len(urls))


@router.post("/add")
async def add_webhook(request: WebhookURLRequest):
    """Add a webhook URL"""
    webhook_service = get_webhook_service()
    webhook_service.add_webhook_url(str(request.url))

    return {
        "success": True,
        "message": f"Webhook URL added: {request.url}",
        "total_webhooks": len(webhook_service.get_webhook_urls()),
    }


@router.post("/remove")
async def remove_webhook(request: WebhookURLRequest):
    """Remove a webhook URL"""
    webhook_service = get_webhook_service()
    webhook_service.remove_webhook_url(str(request.url))

    return {
        "success": True,
        "message": f"Webhook URL removed: {request.url}",
        "total_webhooks": len(webhook_service.get_webhook_urls()),
    }


@router.post("/test")
async def test_webhook(request: WebhookTestRequest):
    """Test a webhook by sending a sample event"""
    webhook_service = get_webhook_service()

    # Create test event
    test_data = {
        "entity_id": "test-entity-123",
        "entity_type": "person",
        "data": {"test": True, "message": "This is a test webhook event"},
    }

    # Temporarily add URL if not already configured
    urls = webhook_service.get_webhook_urls()
    url_str = str(request.url)
    temp_added = False

    if url_str not in urls:
        webhook_service.add_webhook_url(url_str)
        temp_added = True

    try:
        # Send test event
        await webhook_service.publish_event(
            request.event_type, "test-entity-123", "person", test_data, "test_user"
        )

        return {
            "success": True,
            "message": f"Test event sent to {request.url}",
            "event_type": request.event_type,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to send test webhook: {str(e)}"
        )

    finally:
        # Remove temporary URL
        if temp_added:
            webhook_service.remove_webhook_url(url_str)


@router.get("/event-types")
async def list_event_types():
    """List all available webhook event types"""
    return {
        "event_types": [
            {
                "type": event_type.value,
                "description": event_type.value.replace(".", " ").title(),
            }
            for event_type in EntityEventType
        ]
    }

"""
Metrics endpoint for Prometheus scraping.
"""

from fastapi import APIRouter, Response

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def get_metrics():
    """
    Expose metrics in Prometheus format.

    This endpoint is scraped by Prometheus to collect application metrics.
    """
    # For now, return a simple response
    # In a production setup, we would integrate with the OTLP metrics exporter
    # Since we're using OTLP HTTP exporter, Prometheus should scrape the collector instead

    return Response(
        content="# imi Metrics\n# Metrics are exported via OpenTelemetry to the collector\n",
        media_type="text/plain; version=0.0.4",
    )

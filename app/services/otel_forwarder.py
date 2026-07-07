#!/usr/bin/env python3
"""
OTEL Telemetry Forwarder Service

This service runs as a separate process and forwards OpenTelemetry telemetry
from localhost:4318 to the dev server via Tailscale proxy.

Architecture:
OTEL Exporters → localhost:4318 → This Forwarder → Tailscale Proxy → Dev Server

This allows clean traffic separation:
- OTEL telemetry goes through Tailscale
- All other HTTP traffic (Claude, GitHub) goes direct to internet
"""

import asyncio
import logging
import os
import sys

import aiohttp
from aiohttp import web

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("otel_forwarder")

class OTELForwarder:
    """Forwards OTEL telemetry requests through Tailscale proxy."""

    def __init__(self, target_host: str | None = None, target_port: int = 4318):
        if not target_host:
            target_host = os.getenv("OTEL_FORWARDER_TARGET_HOST", "127.0.0.1")
        self.target_host = target_host
        self.target_port = target_port
        self.target_base = f"http://{target_host}:{target_port}"
        self.tailscale_proxy = "http://127.0.0.1:1055"

        # Stats tracking
        self.requests_forwarded = 0
        self.requests_failed = 0

    async def forward_request(self, request: web.Request) -> web.Response:
        """Forward an OTEL request to the target server via Tailscale proxy."""

        target_url = f"{self.target_base}{request.path_qs}"

        logger.debug(f"Forwarding {request.method} {request.path_qs} to {target_url}")

        try:
            # Create session with Tailscale proxy
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:

                # Forward the request through Tailscale proxy
                async with session.request(
                    method=request.method,
                    url=target_url,
                    headers=dict(request.headers),
                    data=await request.read(),
                    proxy=self.tailscale_proxy
                ) as response:

                    # Read response body
                    body = await response.read()

                    # Create response with same status and headers
                    forwarded_response = web.Response(
                        body=body,
                        status=response.status,
                        headers=dict(response.headers)
                    )

                    self.requests_forwarded += 1
                    logger.debug(f"Forwarded successfully: {response.status}")

                    return forwarded_response

        except TimeoutError:
            self.requests_failed += 1
            logger.error(f"Timeout forwarding request to {target_url}")
            return web.Response(
                text="Gateway Timeout: Could not reach OTEL collector",
                status=504
            )

        except aiohttp.ClientConnectorError as e:
            self.requests_failed += 1
            logger.error(f"Connection error forwarding to {target_url}: {e}")
            return web.Response(
                text="Bad Gateway: Could not connect to OTEL collector",
                status=502
            )

        except Exception as e:
            self.requests_failed += 1
            logger.error(f"Unexpected error forwarding to {target_url}: {e}")
            return web.Response(
                text="Internal Server Error in OTEL forwarder",
                status=500
            )

    async def health_check(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        status = {
            "status": "healthy",
            "target": self.target_base,
            "proxy": self.tailscale_proxy,
            "requests_forwarded": self.requests_forwarded,
            "requests_failed": self.requests_failed
        }
        return web.json_response(status)

def create_app() -> web.Application:
    """Create the OTEL forwarder application."""

    # Get configuration from environment
    target_host = os.getenv("OTEL_FORWARDER_TARGET_HOST", "127.0.0.1")
    target_port = int(os.getenv("OTEL_FORWARDER_TARGET_PORT", "4318"))

    forwarder = OTELForwarder(target_host, target_port)

    # Create aiohttp application
    app = web.Application()

    # Health check endpoint
    app.router.add_get("/_health", forwarder.health_check)

    # Forward all other requests
    app.router.add_route("*", "/{path:.*}", forwarder.forward_request)

    return app

async def main():
    """Main entry point."""

    # Check if Tailscale is enabled
    if os.getenv("TAILSCALE_ADMIN_ENABLED", "false").lower() != "true":
        logger.info("Tailscale not enabled, OTEL forwarder not needed")
        return

    logger.info("Starting OTEL forwarder service")
    target = os.getenv("OTEL_FORWARDER_TARGET_HOST", "127.0.0.1")
    logger.info(f"Forwarding localhost:4318 → {target}:4318 via Tailscale proxy")

    app = create_app()

    # Start the server
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "127.0.0.1", 4318)
    await site.start()

    logger.info("OTEL forwarder listening on 127.0.0.1:4318")

    # Keep running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Shutting down OTEL forwarder")
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    # Install signal handlers for graceful shutdown
    import signal

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Run the forwarder
    asyncio.run(main())

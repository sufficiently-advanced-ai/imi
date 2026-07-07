#!/bin/bash
# Hot-reload development mode - edit files on host, see changes instantly
#
# Usage:
#   ./dev-hot.sh              # Build image + start with hot-reload
#   ./dev-hot.sh --no-build   # Skip build, just (re)start containers
#   ./dev-hot.sh --rebuild    # Force full rebuild with --no-cache
#
# What auto-reloads:
#   - app/**/*.py        → uvicorn detects changes, restarts (~1-2s)
#   - imi-frontend/app/   → Next.js Turbopack HMR (~instant)
#   - imi-frontend/components/ → Next.js Turbopack HMR (~instant)
#   - imi-frontend/lib/   → Next.js Turbopack HMR (~instant)
#   - tests/**/*.py      → Available immediately for pytest
#   - config/            → Available immediately (read on access)
#
# What still needs a rebuild:
#   - requirements.txt   → pip install runs at build time
#   - package.json       → npm ci runs at build time
#   - Dockerfile.dev     → Image structure changes
#   - nginx configs      → Baked into image

# Docker Compose command shim - support both v1 and v2
if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    COMPOSE="docker compose"
fi

# Auto-detect instance name from current directory
INSTANCE_NAME="${INSTANCE_NAME:-$(basename "$(pwd)")}"
INSTANCE_NAME="$(echo "$INSTANCE_NAME" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9._-]+/-/g; s/-+/-/g; s/^-+//; s/-+$//')"
if [ -z "$INSTANCE_NAME" ]; then
    INSTANCE_NAME="imi"
fi

# Read DEV_PORT from .env if it exists
if [ -f .env ] && [ -z "${DEV_PORT:-}" ]; then
    DEV_PORT=$(grep "^DEV_PORT=" .env | sed -E 's/^DEV_PORT=//; s/^["'"'"']//; s/["'"'"']$//' | tr -d ' ')
fi
if [ -z "$DEV_PORT" ]; then
    DEV_PORT=8080
    while lsof -Pi ":$DEV_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; do
        DEV_PORT=$((DEV_PORT + 1))
    done
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

COMPOSE_FILE="docker-compose.dev-hot.yml"

echo -e "${CYAN}=== Hot-Reload Development Mode ===${NC}"
echo -e "${GREEN}Instance: ${INSTANCE_NAME}${NC}"
echo -e "${GREEN}Port: ${DEV_PORT}${NC}"
echo ""

# Render per-instance .mcp.json from template — keeps Claude Code's MCP URL
# in sync with $DEV_PORT so each instance always points at its own container.
if [ -f .mcp.json.example ]; then
    expected_url="http://127.0.0.1:${DEV_PORT}/api/mcp/sse"
    if [ ! -f .mcp.json ]; then
        sed "s|\${DEV_PORT}|${DEV_PORT}|g" .mcp.json.example > .mcp.json
        echo -e "${GREEN}Rendered .mcp.json from template (DEV_PORT=${DEV_PORT})${NC}"
    elif ! grep -qF "$expected_url" .mcp.json; then
        echo -e "${YELLOW}.mcp.json port mismatch (expected ${DEV_PORT}) — re-rendering${NC}"
        sed "s|\${DEV_PORT}|${DEV_PORT}|g" .mcp.json.example > .mcp.json
    fi
fi

# Parse arguments
SKIP_BUILD=false
FORCE_REBUILD=false
for arg in "$@"; do
    case "$arg" in
        --no-build) SKIP_BUILD=true ;;
        --rebuild) FORCE_REBUILD=true ;;
    esac
done

# Stop existing containers
echo -e "${YELLOW}Stopping existing containers...${NC}"
INSTANCE_NAME=${INSTANCE_NAME} DEV_PORT=${DEV_PORT} ${COMPOSE} -f ${COMPOSE_FILE} down 2>/dev/null || true
# Also stop the regular dev compose in case it's running
INSTANCE_NAME=${INSTANCE_NAME} DEV_PORT=${DEV_PORT} ${COMPOSE} -f docker-compose.dev.yml down 2>/dev/null || true

if [ "$SKIP_BUILD" = true ]; then
    echo -e "${YELLOW}Skipping build (--no-build)${NC}"
elif [ "$FORCE_REBUILD" = true ]; then
    echo -e "${GREEN}Force rebuilding image (--no-cache)...${NC}"
    docker build --no-cache \
        --build-arg NEXT_PUBLIC_API_URL=/api \
        --build-arg NEXT_PUBLIC_AUTH_MODE=demo \
        -f Dockerfile.dev \
        -t ${INSTANCE_NAME} .
else
    # Smart build: use cache if image exists, otherwise full build
    if docker image inspect ${INSTANCE_NAME} >/dev/null 2>&1; then
        echo -e "${GREEN}Image exists, using cached build...${NC}"
        docker build \
            --build-arg NEXT_PUBLIC_API_URL=/api \
            --build-arg NEXT_PUBLIC_AUTH_MODE=demo \
            -f Dockerfile.dev \
            -t ${INSTANCE_NAME} .
    else
        echo -e "${GREEN}No image found, building from scratch...${NC}"
        docker build --no-cache \
            --build-arg NEXT_PUBLIC_API_URL=/api \
            --build-arg NEXT_PUBLIC_AUTH_MODE=demo \
            -f Dockerfile.dev \
            -t ${INSTANCE_NAME} .
    fi
fi

if [ $? -ne 0 ] && [ "$SKIP_BUILD" != true ]; then
    echo -e "${RED}Build failed!${NC}"
    exit 1
fi

# Start with hot-reload compose
echo -e "${GREEN}Starting with hot-reload...${NC}"
INSTANCE_NAME=${INSTANCE_NAME} DEV_PORT=${DEV_PORT} ${COMPOSE} -f ${COMPOSE_FILE} up -d

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}=== Hot-Reload Dev Server Running ===${NC}"
    echo ""
    echo -e "  ${CYAN}URL:${NC}  http://localhost:${DEV_PORT}/"
    echo -e "  ${CYAN}Container:${NC}  ${INSTANCE_NAME}-dev"
    echo ""
    echo -e "${YELLOW}Auto-reloading:${NC}"
    echo "  app/**/*.py             → Backend reloads (~1-2s)"
    echo "  imi-frontend/app/        → Frontend HMR (instant)"
    echo "  imi-frontend/components/ → Frontend HMR (instant)"
    echo "  imi-frontend/lib/        → Frontend HMR (instant)"
    echo "  tests/                  → Picked up by pytest"
    echo "  config/                 → Read on access"
    echo ""
    echo -e "${GREEN}Commands:${NC}"
    echo "  Logs:     ${COMPOSE} -f ${COMPOSE_FILE} logs -f"
    echo "  Stop:     ${COMPOSE} -f ${COMPOSE_FILE} down"
    echo "  Restart:  ${COMPOSE} -f ${COMPOSE_FILE} restart app"
    echo "  Tests:    docker exec ${INSTANCE_NAME}-dev pytest tests/ -xvs"
    echo ""
    echo -e "${YELLOW}Tip: Use './dev-hot.sh --no-build' for instant restarts${NC}"
else
    echo -e "${RED}Failed to start containers!${NC}"
    exit 1
fi

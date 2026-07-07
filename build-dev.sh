#!/bin/bash
# Build and start script for development Docker image with demo auth mode
# 
# This script:
# 1. Builds the development monocontainer with demo authentication
# 2. Automatically starts the container after successful build
# 3. Auto-detects instance name from current directory
# 4. Reads port configuration from .env file

# Docker Compose command shim - support both v1 and v2
if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    COMPOSE="docker compose"
fi

# Auto-detect instance name from current directory
INSTANCE_NAME="${INSTANCE_NAME:-$(basename "$(pwd)")}"

# Sanitize instance name for Docker compatibility (lowercase, valid chars only)
INSTANCE_NAME="$(echo "$INSTANCE_NAME" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9._-]+/-/g; s/-+/-/g; s/^-+//; s/-+$//')"

# Fallback if empty after sanitization
if [ -z "$INSTANCE_NAME" ]; then
    INSTANCE_NAME="imi"
fi

# Try to read DEV_PORT from .env if it exists (handle quotes and spaces)
if [ -f .env ] && [ -z "${DEV_PORT:-}" ]; then
    DEV_PORT=$(grep "^DEV_PORT=" .env | sed -E 's/^DEV_PORT=//; s/^["'"'"']//; s/["'"'"']$//' | tr -d ' ')
fi

# If still no port, find next available port starting from 8080
if [ -z "$DEV_PORT" ]; then
    DEV_PORT=8080
    while lsof -Pi ":$DEV_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; do
        DEV_PORT=$((DEV_PORT + 1))
    done
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Building development container for: ${INSTANCE_NAME}${NC}"
echo -e "${YELLOW}Auth mode: demo (no external identity provider required)${NC}"
echo -e "${YELLOW}Architecture: Monocontainer without basePath${NC}"
echo -e "${YELLOW}Port: ${DEV_PORT}${NC}"
echo ""

# Stop existing containers if running
echo -e "${YELLOW}Stopping existing containers...${NC}"
${COMPOSE} down 2>/dev/null || true
${COMPOSE} -f docker-compose.dev.yml down 2>/dev/null || true

# Build the Docker image with demo auth
echo -e "${GREEN}Building development image...${NC}"
docker build --no-cache \
  --build-arg NEXT_PUBLIC_API_URL=/api \
  --build-arg NEXT_PUBLIC_AUTH_MODE=demo \
  -f Dockerfile.dev \
  -t ${INSTANCE_NAME} .

# Check if build succeeded
if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✓ Build complete!${NC}"
    echo ""
    echo "The development container has been built with:"
    echo "  - Demo authentication mode (no external identity provider required)"
    echo "  - Combined frontend + backend in single container"
    echo "  - Nginx proxy on port 8080 (internal)"
    echo "  - No basePath - runs at root"
    echo ""
    
    # Start the container automatically
    echo -e "${GREEN}Starting the development container...${NC}"
    DEV_PORT=${DEV_PORT} ${COMPOSE} -f docker-compose.dev.yml up -d
    
    # Check if container started successfully
    if [ $? -eq 0 ]; then
        echo ""
        echo -e "${GREEN}✓ Container started successfully!${NC}"
        echo ""
        echo -e "${GREEN}Access the application at:${NC}"
        echo "  http://localhost:${DEV_PORT}/"
        echo "  https://your-server.example.com/${INSTANCE_NAME}/"
        echo ""
        echo -e "${GREEN}Useful commands:${NC}"
        echo "  View logs:    ${COMPOSE} -f docker-compose.dev.yml logs -f"
        echo "  Stop:         ${COMPOSE} -f docker-compose.dev.yml down"
        echo "  Restart:      ${COMPOSE} -f docker-compose.dev.yml restart"
        echo "  Container:    ${INSTANCE_NAME}-dev"
        echo ""
        echo -e "${YELLOW}Note: This instance ($INSTANCE_NAME) is using port: ${DEV_PORT}${NC}"
    else
        echo ""
        echo -e "${RED}✗ Failed to start container!${NC}"
        echo "Please check Docker logs for more information."
        exit 1
    fi
else
    echo ""
    echo -e "${RED}✗ Build failed!${NC}"
    echo "Please check the error messages above."
    exit 1
fi
#!/bin/bash
set -e

echo "Starting imi Production Container..."

# Ensure npm global binaries are in PATH for Claude Agent SDK CLI
# The nikolaik/python-nodejs base image uses /usr/local/bin for global npm packages
export PATH="/usr/local/bin:$PATH"

# SDK will handle CLI subprocess initialization and configuration
echo "Claude Agent SDK will manage CLI subprocess..."

# Configure Claude Agent SDK authentication
echo "Configuring Claude Agent SDK authentication..."

# Create .claude directory if it doesn't exist
mkdir -p ~/.claude

# Dev mode: Check if subscription credentials are mounted from host
if [ -f ~/.claude/.credentials.json ]; then
    echo "Dev mode detected: Using subscription credentials from host"
    # Create config file with ONLY onboarding flag (no API key)
    # Subscription auth comes from .credentials.json
    cat > ~/.claude/.claude.json <<EOF
{
  "hasCompletedOnboarding": true
}
EOF
    echo "✓ Claude Agent SDK configured for subscription auth"
else
    # Production mode: Use API key from environment variable
    echo "Production mode: Using API key authentication"
    if [ -z "${ANTHROPIC_API_KEY}" ]; then
        echo "ERROR: ANTHROPIC_API_KEY not set - Claude Agent SDK will not work"
        exit 1
    fi

    # Create authentication file with API key
    cat > ~/.claude/.claude.json <<EOF
{
  "hasCompletedOnboarding": true,
  "primaryApiKey": "${ANTHROPIC_API_KEY}"
}
EOF
    echo "✓ Claude Agent SDK authentication configured"
fi

# Set secure permissions (read/write for owner only)
chmod 600 ~/.claude/.claude.json

# Export INSTANCE_NAME with fallback for supervisord
export INSTANCE_NAME=${INSTANCE_NAME:-$(hostname)}

# Create necessary directories
echo "Creating required directories..."
mkdir -p /app/repo
mkdir -p /var/log/nginx
mkdir -p /var/cache/nginx

# One-time data-file migration: the default SQLite path changed from
# kb-llm.db to imi.db when the old codename was retired. Deployments that
# never set DATABASE_PATH/DATABASE_URL would otherwise boot against a new
# empty database after upgrading; move the existing file forward instead.
if [ -z "${DATABASE_PATH:-}" ] && [ -z "${DATABASE_URL:-}" ] \
   && [ -f /app/data/kb-llm.db ] && [ ! -f /app/data/imi.db ]; then
    echo "Renaming SQLite data file kb-llm.db -> imi.db (default path rename)"
    # Best-effort: a failed rename must not abort boot (set -e), and the
    # WAL/SHM/journal sidecars must follow the main file if present.
    if mv /app/data/kb-llm.db /app/data/imi.db; then
        for ext in -wal -shm -journal; do
            if [ -f "/app/data/kb-llm.db${ext}" ]; then
                mv "/app/data/kb-llm.db${ext}" "/app/data/imi.db${ext}" \
                    || echo "WARNING: could not move kb-llm.db${ext} sidecar"
            fi
        done
    else
        echo "WARNING: could not rename kb-llm.db -> imi.db; starting against a new database at the imi.db default path"
    fi
fi

# Ensure proper permissions
chown -R www-data:www-data /var/cache/nginx
chown -R www-data:www-data /var/log/nginx

# Clean up any stale pid files
echo "Cleaning up stale pid files..."
rm -f /tmp/supervisord.pid

# (Tailscale admin tooling is a hosted-edition feature; not included here.)
# Environment check
echo "Environment configuration:"
echo "  - API running on: localhost:8000"
echo "  - Frontend running on: localhost:3000"
echo "  - Nginx listening on: 0.0.0.0:8080"
echo "  - NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-/api}"

echo "Starting services with supervisord..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
#!/usr/bin/with-contenv bashio
set -euo pipefail

export PAPERLESS_URL="$(bashio::config 'paperless_url')"
export PAPERLESS_TOKEN="$(bashio::config 'paperless_token')"
export MCP_TOKEN="$(bashio::config 'mcp_token')"
export READ_ONLY="$(bashio::config 'read_only')"

exec python3 /app/server.py

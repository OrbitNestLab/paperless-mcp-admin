#!/usr/bin/with-contenv bashio
set -euo pipefail

export PAPERLESS_URL="$(bashio::config 'paperless_url')"
export PAPERLESS_TOKEN="$(bashio::config 'paperless_token')"
export MCP_TOKEN="$(bashio::config 'mcp_token')"
export READ_ONLY="$(bashio::config 'read_only')"
export AUTH_MODE="$(bashio::config 'auth_mode')"
export GITHUB_CLIENT_ID="$(bashio::config 'github_client_id')"
export GITHUB_CLIENT_SECRET="$(bashio::config 'github_client_secret')"
export GITHUB_ALLOWED_USER_ID="$(bashio::config 'github_allowed_user_id')"
export OAUTH_SIGNING_KEY="$(bashio::config 'oauth_signing_key')"

umask 077
export XDG_DATA_HOME=/data/fastmcp-oauth
mkdir -p "$XDG_DATA_HOME"

exec python3 /app/server.py

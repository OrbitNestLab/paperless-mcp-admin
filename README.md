# Paperless MCP Admin – FamilieFlips

Home Assistant App for the Paperless-ngx MCP endpoint `https://mcp.familieflips.com/mcp`.

## Security
- Default: `auth_mode: static`, `read_only: true`.
- Optional GitHub OAuth (FastMCP OAuth proxy): `auth_mode: github`.
- OAuth requires a GitHub OAuth App and restricts logins to the configured immutable GitHub user ID.
- Refresh tokens and registered clients are stored encrypted in the add-on's persistent `/data` directory; keep `oauth_signing_key` stable.
- Never store a Paperless token, OAuth client secret, MCP bearer token, or signing key in Git.

## Set up GitHub OAuth
1. Open https://github.com/settings/applications/new and register an **OAuth App**.
2. Set **Application name** to `FamilieFlips Paperless MCP`.
3. Set **Homepage URL** to `https://mcp.familieflips.com`.
4. Set **Authorization callback URL** to `https://mcp.familieflips.com/auth/callback`.
5. Generate a GitHub OAuth client secret.
6. In Home Assistant > Settings > Apps > Paperless MCP Admin > Configuration, enter `github_client_id` and `github_client_secret`. Do not paste the secret into chat.
7. If using Cloudflare Tunnel, set Managed OAuth to off on the Access application for this hostname so the MCP server publishes its own OAuth endpoints and validates its own tokens. Verify the public OAuth issuer is `https://mcp.familieflips.com/` and an unauthenticated MCP request receives HTTP 401.
8. Switch `auth_mode` to `github`, restart the add-on and verify the public OAuth metadata, user login, refresh flow, and document access. Finally reconnect the ChatGPT MCP plugin using OAuth.
9. Set `read_only: false` to enable writes for the configured account. The Paperless service account must have the required permissions for each operation.

## Write tools

- Use `update_document` to change document fields and `upload_document` to import a file or add a file version.
- Use `run_document_operation` for notes, versions, document processing, bulk editing, and trash operations.
- Use `create_admin_object`, `change_admin_object`, or `paperless_admin_write` to manage Paperless objects such as tags, document types, correspondents, storage paths, custom fields, workflows, mail rules, users, and groups.

## Troubleshooting
- A 401 from the Paperless API means an invalid/missing Paperless API token.
- A 401 from the MCP endpoint means invalid MCP credentials.
- Cloudflare Access can cause independent reauthentication due to its session duration; its Access app must be adjusted separately when OAuth is enabled.

The fixed public MCP server endpoint is `https://mcp.familieflips.com/mcp`.

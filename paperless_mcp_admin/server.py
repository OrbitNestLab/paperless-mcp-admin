import base64
import os
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.server.auth import OAuthProxy
from fastmcp.server.auth.providers.github import GitHubTokenVerifier
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier


PAPERLESS_URL = os.environ["PAPERLESS_URL"].rstrip("/")
PAPERLESS_TOKEN = os.environ["PAPERLESS_TOKEN"]
MCP_TOKEN = os.environ["MCP_TOKEN"]
READ_ONLY = os.environ.get("READ_ONLY", "true").lower() in {"true", "1", "yes"}

if not PAPERLESS_TOKEN or len(MCP_TOKEN) < 32:
    raise SystemExit("A Paperless API token and an independent MCP token (32+ characters) are required.")

ALLOWED_COLLECTIONS = {
    "document_types", "storage_paths", "tags", "custom_fields",
    "workflows", "mail_accounts", "mail_rules", "users",
    "groups", "correspondents",
}

AUTH_MODE = os.environ.get("AUTH_MODE", "static").strip().lower()
GITHUB_ALLOWED_USER_ID = os.environ.get("GITHUB_ALLOWED_USER_ID", "").strip()


class OwnerOnlyGitHubTokenVerifier(GitHubTokenVerifier):
    """Allow only the specifically configured GitHub account."""

    async def verify_token(self, token: str):
        access_token = await super().verify_token(token)
        if access_token is None or access_token.claims.get("sub") != GITHUB_ALLOWED_USER_ID:
            return None
        return access_token


if AUTH_MODE == "github":
    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "")
    signing_key = os.environ.get("OAUTH_SIGNING_KEY", "")
    if not client_id or not client_secret or len(signing_key) < 32 or not GITHUB_ALLOWED_USER_ID:
        raise SystemExit("GitHub OAuth requires client ID, client secret, signing key (32+ chars), and allowed user ID.")
    auth = OAuthProxy(
        upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
        upstream_token_endpoint="https://github.com/login/oauth/access_token",
        upstream_client_id=client_id,
        upstream_client_secret=client_secret,
        token_verifier=OwnerOnlyGitHubTokenVerifier(required_scopes=["read:user"]),
        base_url="https://mcp.familieflips.com",
        jwt_signing_key=signing_key,
        allowed_client_redirect_uris=[
            "https://chatgpt.com/connector/oauth/*",
            "https://chatgpt.com/connector_platform_oauth_redirect",
        ],
        require_authorization_consent=True,
    )
elif AUTH_MODE == "static":
    auth = StaticTokenVerifier(
        tokens={MCP_TOKEN: {"client_id": "familieflips", "scopes": ["paperless"]}},
        required_scopes=["paperless"],
    )
else:
    raise SystemExit("Unsupported auth_mode. Use 'static' or 'github'.")

mcp = FastMCP("Paperless FamilieFlips", auth=auth)


def _url(collection: str, item_id: int | None = None) -> str:
    if collection not in ALLOWED_COLLECTIONS:
        raise ValueError("Paperless collection is not permitted.")
    if item_id is not None and item_id < 1:
        raise ValueError("Item ID must be positive.")
    suffix = f"/{item_id}" if item_id is not None else ""
    return f"{PAPERLESS_URL}/api/{collection}{suffix}/"


async def _request(method: str, url: str, payload: Any = None, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(
        timeout=60,
        headers={"Authorization": f"Token {PAPERLESS_TOKEN}"},
        follow_redirects=False,
    ) as client:
        response = await client.request(method, url, json=payload, params=params)
        response.raise_for_status()
        if not response.content:
            return {"status": response.status_code}
        return response.json()


def _write_blocked() -> dict | None:
    return {"status": "blocked", "reason": "Read-only mode is enabled"} if READ_ONLY else None


@mcp.tool()
async def search_documents(
    query: str = "", tag_ids: list[int] | None = None,
    correspondent_id: int | None = None, document_type_id: int | None = None,
    created_after: str | None = None, created_before: str | None = None,
    page_size: int = 25,
) -> dict:
    """Search documents by text and optional filters."""
    params: dict[str, Any] = {"page_size": max(1, min(page_size, 100))}
    if query:
        params["query"] = query
    if tag_ids:
        params["tags__id__all"] = ",".join(map(str, tag_ids))
    if correspondent_id is not None:
        params["correspondent__id"] = correspondent_id
    if document_type_id is not None:
        params["document_type__id"] = document_type_id
    if created_after:
        params["created__date__gte"] = created_after
    if created_before:
        params["created__date__lte"] = created_before
    data = await _request("GET", _url("documents"), params=params) if False else await _request(
        "GET", f"{PAPERLESS_URL}/api/documents/", params=params
    )
    return {
        "count": data.get("count", 0),
        "results": [
            {
                "id": row.get("id"), "title": row.get("title"),
                "created": row.get("created"), "correspondent": row.get("correspondent"),
                "tags": row.get("tags"), "document_type": row.get("document_type"),
                "snippet": (row.get("content") or "")[:300],
            }
            for row in data.get("results", [])
        ],
    }


@mcp.tool()
async def get_document(document_id: int, include_content: bool = True) -> dict:
    """Get document metadata and optionally its extracted text."""
    result = await _request("GET", f"{PAPERLESS_URL}/api/documents/{document_id}/")
    if not include_content:
        result.pop("content", None)
    return result


@mcp.tool()
async def download_document(document_id: int, original: bool = False) -> dict:
    """Download a document as base64. Does not alter the document."""
    async with httpx.AsyncClient(
        timeout=120,
        headers={"Authorization": f"Token {PAPERLESS_TOKEN}"},
        follow_redirects=False,
    ) as client:
        r = await client.get(
            f"{PAPERLESS_URL}/api/documents/{document_id}/download/",
            params={"original": "true" if original else "false"},
        )
        r.raise_for_status()
        return {
            "document_id": document_id,
            "filename": r.headers.get("content-disposition", ""),
            "content_type": r.headers.get("content-type", "application/pdf"),
            "base64": base64.b64encode(r.content).decode("ascii"),
        }


@mcp.tool()
async def list_tags(page_size: int = 200) -> dict:
    """List Paperless tags."""
    return await _request("GET", _url("tags"), params={"page_size": min(max(1, page_size), 500)})


@mcp.tool()
async def list_correspondents(page_size: int = 200) -> dict:
    """List Paperless correspondents."""
    return await _request("GET", _url("correspondents"), params={"page_size": min(max(1, page_size), 500)})


@mcp.tool()
async def list_document_types(page_size: int = 200) -> dict:
    """List Paperless document types."""
    return await _request("GET", _url("document_types"), params={"page_size": min(max(1, page_size), 500)})


@mcp.tool()
async def list_admin_objects(collection: str, page: int = 1) -> dict:
    """List an allowed configuration collection (such as mail_rules)."""
    return await _request("GET", _url(collection), params={"page": max(1, page)})


@mcp.tool()
async def get_admin_object(collection: str, item_id: int) -> dict:
    """Read a configuration item."""
    return await _request("GET", _url(collection, item_id))


@mcp.tool()
async def create_admin_object(collection: str, payload: dict) -> dict:
    """Create a configuration item when write access is enabled."""
    if READ_ONLY:
        return _write_blocked()
    return await _request("POST", _url(collection), payload=payload)


@mcp.tool()
async def change_admin_object(collection: str, item_id: int, payload: dict) -> dict:
    """Patch a configuration item when write access is enabled."""
    if READ_ONLY:
        return _write_blocked()
    return await _request("PATCH", _url(collection, item_id), payload=payload)


@mcp.tool()
async def delete_mail_rule(item_id: int, confirmed: bool = False) -> dict:
    """Delete a mail rule only with an explicit confirmed flag and enabled writes."""
    if not confirmed:
        return {"status": "preview", "item_id": item_id, "action": "DELETE mail rule"}
    if READ_ONLY:
        return _write_blocked()
    return await _request("DELETE", _url("mail_rules", item_id))


# Compatibility tools for the original administration MCP.
@mcp.tool()
async def paperless_admin_list(collection: str, page: int = 1, page_size: int = 100) -> dict:
    """Legacy administrative list endpoint."""
    return await _request("GET", _url(collection), params={"page": max(1, page), "page_size": min(max(1, page_size), 100)})


@mcp.tool()
async def paperless_admin_get(collection: str, item_id: int) -> dict:
    """Legacy administrative read endpoint."""
    return await get_admin_object(collection, item_id)


@mcp.tool()
async def paperless_admin_write(
    method: str, collection: str, item_id: int | None = None,
    payload: dict | None = None, confirm: bool = False,
) -> dict:
    """Legacy guarded administrative write endpoint."""
    method = method.upper()
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError("Unsupported write method")
    url = _url(collection, item_id)
    if READ_ONLY:
        return _write_blocked()
    if not confirm:
        return {"status": "preview", "method": method, "collection": collection, "item_id": item_id}
    return await _request(method, url, payload=payload)


@mcp.tool()
async def paperless_admin_status() -> dict:
    """Check Paperless availability without returning document details."""
    data = await _request("GET", f"{PAPERLESS_URL}/api/documents/", params={"page_size": 1})
    return {"connected": True, "document_count": data.get("count", 0), "read_only": READ_ONLY}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8770, stateless_http=True)

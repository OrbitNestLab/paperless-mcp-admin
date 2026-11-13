import base64
import binascii
import json
import os
import re
from typing import Any, Literal
from urllib.parse import urlsplit

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
    "groups", "correspondents", "saved_views", "workflow_triggers",
    "workflow_actions", "share_links", "share_link_bundles", "config", "documents",
}

DOCUMENTS_URL = f"{PAPERLESS_URL}/api/documents/"


def _allowed_chatgpt_redirect(uri: str) -> bool:
    """Restrict all DCR clients to ChatGPT's documented HTTPS callbacks."""
    try:
        parsed = urlsplit(str(uri))
        if (
            parsed.scheme != "https"
            or parsed.hostname != "chatgpt.com"
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query or parsed.fragment
        ):
            return False
        if parsed.path == "/connector_platform_oauth_redirect":
            return True
        return bool(re.fullmatch(r"/connector/oauth/[A-Za-z0-9_-]{1,128}", parsed.path))
    except ValueError:
        return False


class ChatGPTOnlyOAuthProxy(OAuthProxy):
    """Fail closed on untrusted callbacks, including already registered clients.

    FastMCP 2.14.x can accept disallowed redirects during DCR, so the
    server validates them before registration and before authorization.
    """

    async def register_client(self, client_info):
        redirect_uris = client_info.redirect_uris or []
        if not redirect_uris or not all(
            _allowed_chatgpt_redirect(str(uri)) for uri in redirect_uris
        ):
            raise ValueError("OAuth client redirect URI is not on the ChatGPT allowlist.")
        return await super().register_client(client_info)

    async def get_client(self, client_id):
        client = await super().get_client(client_id)
        if client is None:
            return None
        redirect_uris = client.redirect_uris or []
        if not redirect_uris or not all(
            _allowed_chatgpt_redirect(str(uri)) for uri in redirect_uris
        ):
            return None
        return client


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
    auth = ChatGPTOnlyOAuthProxy(
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


async def _request(
    method: str,
    url: str,
    payload: Any = None,
    params: dict | None = None,
    *,
    form: dict | None = None,
    files: dict | None = None,
) -> Any:
    async with httpx.AsyncClient(
        timeout=60,
        headers={"Authorization": f"Token {PAPERLESS_TOKEN}"},
        follow_redirects=False,
    ) as client:
        response = await client.request(
            method, url, json=payload, params=params, data=form, files=files
        )
        response.raise_for_status()
        if not response.content:
            return {"status": response.status_code}
        return response.json()


def _write_blocked() -> dict:
    return {"status": "blocked", "reason": "Read-only mode is enabled"}


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
    data = await _request("GET", DOCUMENTS_URL, params=params)
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
    result = await _request("GET", f"{DOCUMENTS_URL}{document_id}/")
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
    """List configuration such as workflows, mail_rules, tags, users, or groups."""
    return await _request("GET", _url(collection), params={"page": max(1, page)})


@mcp.tool()
async def get_admin_object(collection: str, item_id: int) -> dict:
    """Read a configuration item."""
    return await _request("GET", _url(collection, item_id))


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
async def create_admin_object(collection: str, payload: dict) -> dict:
    """Create a configuration item when write access is enabled."""
    if READ_ONLY:
        return _write_blocked()
    return await _request("POST", _url(collection), payload=payload)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
async def change_admin_object(collection: str, item_id: int, payload: dict) -> dict:
    """Patch a configuration item when write access is enabled."""
    if READ_ONLY:
        return _write_blocked()
    return await _request("PATCH", _url(collection, item_id), payload=payload)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
async def update_document(document_id: int, changes: dict[str, Any]) -> dict:
    """Change a document's fields, including title, tags, date, storage path,
    custom fields, text, owner or permissions. Only sends supplied fields.
    The tags and custom_fields fields replace the corresponding full lists.
    """
    if READ_ONLY:
        return _write_blocked()
    if not changes:
        raise ValueError("Supply at least one document field to change.")
    updated = await _request("PATCH", _url("documents", document_id), payload=changes)
    updated.pop("content", None)
    return updated


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
async def upload_document(
    filename: str,
    content_b64: str,
    metadata: dict[str, Any] | None = None,
    document_id: int | None = None,
) -> dict:
    """Upload a file to Paperless. For new documents, metadata can include title,
    created, correspondent, document_type, storage_path, tags, archive_serial_number
    and custom_fields. Set document_id to add a file version to an existing document
    and use version_label as the optional metadata. Returns the import task ID.
    """
    if READ_ONLY:
        return _write_blocked()
    if not filename.strip() or any(c in filename for c in ("/", "\\", "\0")):
        raise ValueError("Supply a filename without a directory.")
    try:
        content = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("The document content must be valid base64.") from exc
    if not content:
        raise ValueError("The document file is empty.")

    metadata = metadata or {}
    if document_id is None:
        url = DOCUMENTS_URL + "post_document/"
        allowed_fields = {
            "title", "created", "correspondent", "document_type", "storage_path",
            "tags", "archive_serial_number", "custom_fields",
        }
    else:
        url = _url("documents", document_id) + "update_version/"
        allowed_fields = {"version_label"}
    unknown_fields = metadata.keys() - allowed_fields
    if unknown_fields:
        raise ValueError(f"Unsupported upload fields: {', '.join(sorted(unknown_fields))}")

    form = {key: value for key, value in metadata.items() if value is not None}
    if "custom_fields" in form:
        form["custom_fields"] = json.dumps(form["custom_fields"])
    task = await _request("POST", url, form=form, files={"document": (filename, content)})
    return {"task": task, "filename": filename}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
async def run_document_operation(
    operation: Literal[
        "bulk_edit", "delete", "reprocess", "rotate", "merge", "merge_as_versions",
        "edit_pdf", "remove_password", "add_note", "delete_note",
        "rename_version", "delete_version", "restore", "empty_trash",
    ],
    payload: dict[str, Any],
    document_id: int | None = None,
    version_id: int | None = None,
    confirm: bool = False,
) -> dict | list[dict]:
    """Run a Paperless document operation with its API arguments. Delete moves
    documents to trash. Restore and empty_trash require explicit document IDs.
    Empty_trash permanently deletes those items. Without confirm, previews the operation.
    """
    if READ_ONLY:
        return _write_blocked()

    method = "POST"
    params = None
    if operation in {"restore", "empty_trash"}:
        documents = payload.get("documents")
        if not isinstance(documents, list) or not documents:
            raise ValueError("Supply the document IDs in payload.documents.")
        url = f"{PAPERLESS_URL}/api/trash/"
        payload = {"documents": documents, "action": "restore" if operation == "restore" else "empty"}
    elif operation in {"add_note", "delete_note", "rename_version", "delete_version"}:
        if document_id is None:
            raise ValueError("Supply the document ID for this operation.")
        document_url = _url("documents", document_id)
        if operation in {"add_note", "delete_note"}:
            url = document_url + "notes/"
            if operation == "delete_note":
                note_id = payload.get("id")
                if type(note_id) is not int or note_id < 1:
                    raise ValueError("Supply the note ID in payload.id.")
                method = "DELETE"
                params = {"id": note_id}
                payload = None
        else:
            if version_id is None or version_id < 1:
                raise ValueError("Supply the version ID for this operation.")
            url = f"{document_url}versions/{version_id}/"
            method = "PATCH" if operation == "rename_version" else "DELETE"
    else:
        url = f"{DOCUMENTS_URL}{operation}/"

    if not confirm:
        return {
            "status": "preview", "operation": operation,
            "document_id": document_id, "version_id": version_id,
            "document_ids": payload.get("documents") if payload else None,
            "note_id": params["id"] if params else None,
        }
    return await _request(method, url, payload=payload, params=params)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
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


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
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
    data = await _request("GET", DOCUMENTS_URL, params={"page_size": 1})
    return {"connected": True, "document_count": data.get("count", 0), "read_only": READ_ONLY}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8770, stateless_http=True)

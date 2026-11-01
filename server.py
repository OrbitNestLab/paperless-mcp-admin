import os
from typing import Any

import httpx
from fastmcp import FastMCP


PAPERLESS_URL = os.environ["PAPERLESS_URL"].rstrip("/")
PAPERLESS_TOKEN = os.environ["PAPERLESS_TOKEN"]
MCP_TOKEN = os.environ["MCP_TOKEN"]
READ_ONLY = os.environ.get("READ_ONLY", "true").lower() == "true"

ALLOWED_COLLECTIONS = {
    "document_types",
    "storage_paths",
    "tags",
    "custom_fields",
    "workflows",
    "mail_accounts",
    "mail_rules",
    "users",
    "groups",
}

mcp = FastMCP("Paperless MCP Admin")


def _url(collection: str, item_id: int | None = None) -> str:
    if collection not in ALLOWED_COLLECTIONS:
        raise ValueError("Nicht erlaubter Paperless-Bereich")
    suffix = f"/{item_id}" if item_id is not None else ""
    return f"{PAPERLESS_URL}/api/{collection}{suffix}/"


async def _request(method: str, url: str, payload: Any | None = None) -> Any:
    headers = {"Authorization": f"Token {PAPERLESS_TOKEN}"}
    async with httpx.AsyncClient(timeout=60, headers=headers) as client:
        response = await client.request(method, url, json=payload)
        response.raise_for_status()
        if not response.content:
            return {"status": response.status_code}
        return response.json()


@mcp.tool()
async def paperless_admin_list(collection: str, page: int = 1, page_size: int = 100) -> dict:
    """Liest eine erlaubte Paperless-Konfigurationssammlung."""
    if collection not in ALLOWED_COLLECTIONS:
        raise ValueError(f"Sammlung nicht erlaubt: {collection}")
    return await _request("GET", f"{_url(collection)}?page={page}&page_size={page_size}")


@mcp.tool()
async def paperless_admin_get(collection: str, item_id: int) -> dict:
    """Liest ein einzelnes Konfigurationsobjekt."""
    return await _request("GET", _url(collection, item_id))


@mcp.tool()
async def paperless_admin_write(
    method: str,
    collection: str,
    item_id: int | None = None,
    payload: dict | None = None,
    confirm: bool = False,
) -> dict:
    """Erstellt, ändert oder löscht ein Objekt; Vorschau ohne confirm=true."""
    method = method.upper()
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError("Nur POST, PUT, PATCH oder DELETE erlaubt")
    if READ_ONLY:
        return {"status": "blocked", "reason": "read_only=true"}
    if not confirm:
        return {
            "status": "preview",
            "method": method,
            "url": _url(collection, item_id),
            "payload": payload or {},
            "next_step": "Erneut mit confirm=true aufrufen",
        }
    return await _request(method, _url(collection, item_id), payload)


@mcp.tool()
async def paperless_admin_status() -> dict:
    """Ermittelt den Gesamtbestand über den erlaubten Such-Fallback."""
    return await _request("GET", f"{PAPERLESS_URL}/api/documents/?page=1&page_size=1&query=*")


if __name__ == "__main__":
    if not PAPERLESS_TOKEN or not MCP_TOKEN:
        raise SystemExit("paperless_token und mcp_token müssen gesetzt sein")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8770)

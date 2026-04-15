"""Google Drive connector — list, upload, download, and manage files."""
from __future__ import annotations

import json


from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector
from agentix.connectors.builtin._google_auth import _build_client

_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]

_BASE = "https://www.googleapis.com/drive/v3"
_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"

_ACTIONS = [
    ConnectorAction("list_files", "List files in Google Drive",
        {"type": "object",
         "properties": {
             "query":       {"type": "string", "description": "Drive search query (e.g. \"name contains 'report'\")"},
             "folder_id":   {"type": "string", "description": "List files within a specific folder ID"},
             "max_results": {"type": "integer", "description": "Maximum results to return (default 20)"},
             "order_by":    {"type": "string", "description": "Sort order (e.g. 'modifiedTime desc')"},
         }, "required": []}),

    ConnectorAction("get_file", "Get metadata for a file by ID",
        {"type": "object",
         "properties": {
             "file_id": {"type": "string", "description": "Google Drive file ID"},
         }, "required": ["file_id"]}),

    ConnectorAction("download_file", "Download a file's text content",
        {"type": "object",
         "properties": {
             "file_id": {"type": "string", "description": "Google Drive file ID"},
         }, "required": ["file_id"]}),

    ConnectorAction("create_folder", "Create a new folder in Google Drive",
        {"type": "object",
         "properties": {
             "name":      {"type": "string", "description": "Folder name"},
             "parent_id": {"type": "string", "description": "Parent folder ID (defaults to root)"},
         }, "required": ["name"]}),

    ConnectorAction("upload_file", "Upload text content as a file to Google Drive",
        {"type": "object",
         "properties": {
             "name":      {"type": "string", "description": "File name including extension"},
             "content":   {"type": "string", "description": "Text content to upload"},
             "parent_id": {"type": "string", "description": "Parent folder ID (defaults to root)"},
             "mime_type": {"type": "string", "description": "MIME type (default: text/plain)"},
         }, "required": ["name", "content"]}),

    ConnectorAction("delete_file", "Move a file to trash",
        {"type": "object",
         "properties": {
             "file_id": {"type": "string", "description": "Google Drive file ID"},
         }, "required": ["file_id"]}),

    ConnectorAction("share_file", "Share a file with a user or make it public",
        {"type": "object",
         "properties": {
             "file_id": {"type": "string", "description": "Google Drive file ID"},
             "email":   {"type": "string", "description": "Email to share with (omit for public link)"},
             "role":    {"type": "string", "enum": ["reader", "commenter", "writer"], "description": "Permission role"},
             "public":  {"type": "boolean", "description": "Make file publicly readable"},
         }, "required": ["file_id"]}),
]


@register_connector("google_drive")
class GoogleDriveConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="google_drive", display_name="Google Drive",
        description="List, upload, download, and manage files in Google Drive.",
        category="google", icon="📁", auth_type="oauth2",
        required_config=["credentials_json"],
        optional_config=["access_token"],
        actions=_ACTIONS,
    )

    def _client(self):
        return _build_client(self._cfg, _SCOPES)

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.get(f"{_BASE}/about", params={"fields": "user"})
            r.raise_for_status()

    async def list_files(
        self, query: str = "", folder_id: str = "",
        max_results: int = 20, order_by: str = "modifiedTime desc",
    ) -> dict:
        q_parts = ["trashed = false"]
        if query:
            q_parts.append(f"({query})")
        if folder_id:
            q_parts.append(f"'{folder_id}' in parents")

        params = {
            "q": " and ".join(q_parts),
            "pageSize": max_results,
            "orderBy": order_by,
            "fields": "files(id,name,mimeType,size,modifiedTime,webViewLink,parents)",
        }
        async with self._client() as c:
            r = await c.get(f"{_BASE}/files", params=params)
            r.raise_for_status()
            return {"files": r.json().get("files", [])}

    async def get_file(self, file_id: str) -> dict:
        params = {"fields": "id,name,mimeType,size,modifiedTime,webViewLink,parents,description"}
        async with self._client() as c:
            r = await c.get(f"{_BASE}/files/{file_id}", params=params)
            r.raise_for_status()
            return r.json()

    async def download_file(self, file_id: str) -> dict:
        async with self._client() as c:
            # Check mime type first
            meta = await c.get(f"{_BASE}/files/{file_id}", params={"fields": "mimeType,name"})
            meta.raise_for_status()
            mime = meta.json().get("mimeType", "")
            name = meta.json().get("name", "")

            # Google Docs/Sheets need export
            export_map = {
                "application/vnd.google-apps.document":     ("text/plain", "txt"),
                "application/vnd.google-apps.spreadsheet":  ("text/csv", "csv"),
                "application/vnd.google-apps.presentation": ("text/plain", "txt"),
            }
            if mime in export_map:
                export_mime, _ = export_map[mime]
                r = await c.get(f"{_BASE}/files/{file_id}/export",
                                params={"mimeType": export_mime})
            else:
                r = await c.get(f"{_BASE}/files/{file_id}", params={"alt": "media"})

            r.raise_for_status()
            return {"name": name, "content": r.text, "mime_type": mime}

    async def create_folder(self, name: str, parent_id: str = "") -> dict:
        metadata: dict = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            metadata["parents"] = [parent_id]
        async with self._client() as c:
            r = await c.post(f"{_BASE}/files", json=metadata,
                             params={"fields": "id,name,webViewLink"})
            r.raise_for_status()
            return {"ok": True, **r.json()}

    async def upload_file(
        self, name: str, content: str,
        parent_id: str = "", mime_type: str = "text/plain",
    ) -> dict:
        metadata: dict = {"name": name}
        if parent_id:
            metadata["parents"] = [parent_id]

        # Multipart upload
        meta_bytes = json.dumps(metadata).encode()
        content_bytes = content.encode()
        boundary = "agentix_boundary_123"
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + meta_bytes
            + f"\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n".encode()
            + content_bytes
            + f"\r\n--{boundary}--".encode()
        )

        async with self._client() as c:
            r = await c.post(
                f"{_UPLOAD_BASE}/files",
                content=body,
                params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                headers={
                    "Content-Type": f"multipart/related; boundary={boundary}",
                    "Authorization": c.headers.get("Authorization", ""),
                },
            )
            r.raise_for_status()
            return {"ok": True, **r.json()}

    async def delete_file(self, file_id: str) -> dict:
        async with self._client() as c:
            r = await c.delete(f"{_BASE}/files/{file_id}")
            r.raise_for_status()
            return {"ok": True, "file_id": file_id, "trashed": True}

    async def share_file(
        self, file_id: str, email: str = "",
        role: str = "reader", public: bool = False,
    ) -> dict:
        if public:
            permission = {"type": "anyone", "role": role}
        elif email:
            permission = {"type": "user", "role": role, "emailAddress": email}
        else:
            return {"ok": False, "error": "Provide 'email' or set 'public=true'"}

        async with self._client() as c:
            r = await c.post(
                f"{_BASE}/files/{file_id}/permissions",
                json=permission,
                params={"fields": "id"},
            )
            r.raise_for_status()
            return {"ok": True, "file_id": file_id, "permission_id": r.json().get("id")}

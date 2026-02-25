"""
SharePoint client using MSAL client-credentials flow + Microsoft Graph API.

Azure AD App Registration requirements (admin-consented application permissions):
  - Sites.Read.All
  - Files.Read.All

Graph API docs: https://learn.microsoft.com/en-us/graph/api/overview
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Generator

import msal
import requests
import urllib3

from rag_agent import config

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]

if config.INSECURE:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class SharePointClient:
    """Authenticated SharePoint / Graph API client (confidential client flow)."""

    def __init__(self) -> None:
        if not all([config.SP_TENANT_ID, config.SP_CLIENT_ID, config.SP_CLIENT_SECRET]):
            raise ValueError(
                "SharePoint MSAL credentials are missing. "
                "Set SP_TENANT_ID, SP_CLIENT_ID, SP_CLIENT_SECRET in .env"
            )
        self._msal_app = msal.ConfidentialClientApplication(
            client_id=config.SP_CLIENT_ID,
            client_credential=config.SP_CLIENT_SECRET,
            authority=f"https://login.microsoftonline.com/{config.SP_TENANT_ID}",
        )
        self._session = requests.Session()
        self._session.verify = not config.INSECURE
        self._site_id: str | None = None
        self._drive_id: str | None = None

    # ── Authentication ─────────────────────────────────────────────────────

    def _get_token(self) -> str:
        result = self._msal_app.acquire_token_silent(GRAPH_SCOPES, account=None)
        if not result:
            result = self._msal_app.acquire_token_for_client(scopes=GRAPH_SCOPES)
        if "access_token" not in result:
            error = result.get("error_description") or result.get("error") or str(result)
            raise RuntimeError(f"MSAL authentication failed: {error}")
        return result["access_token"]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    def _get(self, url: str, **kwargs) -> dict:
        resp = self._session.get(url, headers=self._headers(), **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _get_bytes(self, url: str) -> bytes:
        resp = self._session.get(url, headers=self._headers(), allow_redirects=True)
        resp.raise_for_status()
        return resp.content

    # ── Site & Drive resolution ────────────────────────────────────────────

    def get_site_id(self) -> str:
        if self._site_id:
            return self._site_id
        url = f"{GRAPH_BASE}/sites/{config.SP_SITE_HOSTNAME}:{config.SP_SITE_PATH}"
        data = self._get(url)
        self._site_id = data["id"]
        log.info("Resolved site ID: %s", self._site_id)
        return self._site_id

    def get_drive_id(self) -> str:
        if self._drive_id:
            return self._drive_id
        site_id = self.get_site_id()
        data = self._get(f"{GRAPH_BASE}/sites/{site_id}/drives")
        for drive in data.get("value", []):
            if drive.get("name", "").lower() == config.SP_DRIVE_NAME.lower():
                self._drive_id = drive["id"]
                log.info("Resolved drive '%s' → ID: %s", drive["name"], self._drive_id)
                return self._drive_id
        available = [d.get("name") for d in data.get("value", [])]
        raise ValueError(
            f"Drive '{config.SP_DRIVE_NAME}' not found. "
            f"Available drives: {available}"
        )

    # ── File listing ───────────────────────────────────────────────────────

    def _iter_folder(self, folder_url: str) -> Generator[dict, None, None]:
        """Recursively yield file items from a folder, handling pagination."""
        url: str | None = folder_url
        while url:
            data = self._get(url)
            for item in data.get("value", []):
                if item.get("folder"):
                    child_url = (
                        f"{GRAPH_BASE}/drives/{self.get_drive_id()}"
                        f"/items/{item['id']}/children"
                    )
                    yield from self._iter_folder(child_url)
                elif item.get("file"):
                    yield item
            url = data.get("@odata.nextLink")

    def list_all_files(self) -> list[dict]:
        """
        Return file items from the drive.

        If SP_SCAN_FOLDERS is configured, only folders whose names are in that
        list (and their sub-folders) are scanned.  Otherwise the entire drive
        root is scanned (original behaviour).
        """
        drive_id = self.get_drive_id()

        if config.SP_SCAN_FOLDERS:
            files: list[dict] = []
            for folder_name in config.SP_SCAN_FOLDERS:
                url = f"{GRAPH_BASE}/drives/{drive_id}/root:/{folder_name}:/children"
                try:
                    folder_files = list(self._iter_folder(url))
                    log.info(
                        "Folder '%s': found %d files.", folder_name, len(folder_files)
                    )
                    files.extend(folder_files)
                except Exception as exc:
                    log.warning("Could not scan folder '%s': %s", folder_name, exc)
            log.info(
                "Total: %d files across %d folder(s).",
                len(files),
                len(config.SP_SCAN_FOLDERS),
            )
            return files

        root_url = f"{GRAPH_BASE}/drives/{drive_id}/root/children"
        files = list(self._iter_folder(root_url))
        log.info("Found %d files in SharePoint drive.", len(files))
        return files

    def _is_in_scan_folders(self, item: dict) -> bool:
        """
        Return True if the item lives inside one of the configured SP_SCAN_FOLDERS.
        Always returns True when SP_SCAN_FOLDERS is empty (no filter).

        Graph API parentReference.path looks like:
            /drives/{id}/root:/FolderName/SubFolder
        """
        if not config.SP_SCAN_FOLDERS:
            return True
        parent_path: str = item.get("parentReference", {}).get("path", "")
        # Extract the portion after "/root:" → "FolderName/SubFolder" or ""
        after_root = parent_path.split("/root:", 1)[-1].lstrip("/")
        for folder in config.SP_SCAN_FOLDERS:
            if after_root == folder or after_root.startswith(folder + "/"):
                return True
        return False

    # ── Delta / incremental sync ───────────────────────────────────────────

    def get_delta(self) -> tuple[list[dict], list[dict], str]:
        """
        Fetch incremental changes using the Graph API delta endpoint.

        Returns:
            (new_or_modified, deleted, new_delta_link)

        On first call (no saved delta token), returns ALL items as new.
        Subsequent calls return only changes since the last delta token.
        """
        drive_id = self.get_drive_id()
        saved_link = self._load_delta_token()
        url: str | None = saved_link or f"{GRAPH_BASE}/drives/{drive_id}/root/delta"

        new_or_modified: list[dict] = []
        deleted: list[dict] = []
        delta_link = ""

        while url:
            data = self._get(url)
            for item in data.get("value", []):
                if item.get("deleted"):
                    if self._is_in_scan_folders(item):
                        deleted.append(item)
                elif item.get("file"):
                    if self._is_in_scan_folders(item):
                        new_or_modified.append(item)
            url = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink", delta_link)

        log.info(
            "Delta result: %d new/modified, %d deleted.",
            len(new_or_modified),
            len(deleted),
        )
        return new_or_modified, deleted, delta_link

    def download_file(self, item: dict) -> bytes:
        """Download file content bytes for a Graph API item."""
        drive_id = self.get_drive_id()
        url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item['id']}/content"
        return self._get_bytes(url)

    # ── Delta token persistence ────────────────────────────────────────────

    def save_delta_token(self, delta_link: str) -> None:
        path = Path(config.DELTA_TOKEN_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"delta_link": delta_link}))
        log.debug("Delta token saved to %s", path)

    def _load_delta_token(self) -> str:
        path = Path(config.DELTA_TOKEN_PATH)
        if path.exists():
            try:
                return json.loads(path.read_text()).get("delta_link", "")
            except Exception:
                return ""
        return ""

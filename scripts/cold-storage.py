#!/usr/bin/env python3
"""
Move old files from "éist - archive" on Google Drive to DS214play NAS cold storage.

Scans the archive folder on Google Drive for files older than 12 months,
copies them to the Synology NAS via the File Station API (QuickConnect),
verifies the transfer by size, then prints instructions to manually
delete the originals from Google Drive.

Modes:
- (no flags)   → full run: scan → transfer → cleanup instructions
- --scan       → list archive files older than --months, save manifest
- --transfer   → download from Drive, upload to NAS, verify
- --cleanup    → print instructions to delete originals from Google Drive
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import warnings

import requests
from dotenv import load_dotenv

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

QUICKCONNECT_ID = "eistcork"
NAS_SHARE = "music"
NAS_BASE_PATH = f"/{NAS_SHARE}/eist-archive"

DRIVE_FOLDER = "éist - archive"
DRIVE_FOLDER_ID = "1t7_VXfAsGT0lxjnhPWmwNksbGyE8aPRP"

STATE_FILE = "cold-storage-state.json"
SCAN_FILE = "cold-storage-scan.json"


# ---------------------------------------------------------------------------
# Google Drive client
# ---------------------------------------------------------------------------


class GoogleDriveClient:
    DRIVE_API = "https://www.googleapis.com/drive/v3/files"

    TOKEN_REFRESH_INTERVAL = 45 * 60

    def __init__(self) -> None:
        self.token: Optional[str] = None
        self._token_acquired_at: float = 0

    def get_token(self) -> str:
        if self.token and (time.time() - self._token_acquired_at) < self.TOKEN_REFRESH_INTERVAL:
            return self.token
        self.token = None
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            self.token = result.stdout.strip()
            self._token_acquired_at = time.time()
            return self.token

        print("No active gcloud credentials. Authenticating...")
        login = subprocess.run(
            ["gcloud", "auth", "login", "--enable-gdrive-access"],
        )
        if login.returncode != 0:
            print("Error: gcloud authentication failed.", file=sys.stderr)
            sys.exit(1)

        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            print("Error: Could not obtain access token.", file=sys.stderr)
            sys.exit(1)
        self.token = result.stdout.strip()
        self._token_acquired_at = time.time()
        return self.token

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.get_token()}"}

    def _refresh_token_if_needed(self, resp: requests.Response) -> bool:
        if resp.status_code == 401:
            self.token = None
            self.get_token()
            return True
        return False

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        resp = getattr(requests, method)(url, headers=self._headers(), **kwargs)
        if self._refresh_token_if_needed(resp):
            resp = getattr(requests, method)(url, headers=self._headers(), **kwargs)
        resp.raise_for_status()
        return resp

    def verify_folder(self, folder_id: str) -> bool:
        try:
            resp = self._request(
                "get", f"{self.DRIVE_API}/{folder_id}",
                params={"fields": "id,name,mimeType"},
            )
            return resp.json().get("mimeType") == "application/vnd.google-apps.folder"
        except requests.HTTPError:
            return False

    def find_folder(self, name: str, parent_id: Optional[str] = None) -> Optional[str]:
        query = (
            f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
            f"and trashed=false"
        )
        if parent_id:
            query += f" and '{parent_id}' in parents"

        resp = self._request(
            "get", self.DRIVE_API,
            params={"q": query, "fields": "files(id,name)", "spaces": "drive"},
        )
        files = resp.json().get("files", [])
        return files[0]["id"] if files else None

    def list_files_in_folder(self, folder_id: str) -> List[Dict]:
        all_files: List[Dict] = []
        page_token = None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed=false",
                "fields": "nextPageToken,files(id,name,size,mimeType,createdTime,modifiedTime)",
                "spaces": "drive",
                "pageSize": 1000,
            }
            if page_token:
                params["pageToken"] = page_token
            resp = self._request("get", self.DRIVE_API, params=params)
            data = resp.json()
            all_files.extend(data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return all_files

    def list_subfolders(self, folder_id: str) -> List[Dict]:
        all_folders: List[Dict] = []
        page_token = None
        while True:
            params = {
                "q": (
                    f"'{folder_id}' in parents "
                    f"and mimeType='application/vnd.google-apps.folder' "
                    f"and trashed=false"
                ),
                "fields": "nextPageToken,files(id,name)",
                "spaces": "drive",
                "pageSize": 1000,
            }
            if page_token:
                params["pageToken"] = page_token
            resp = self._request("get", self.DRIVE_API, params=params)
            data = resp.json()
            all_folders.extend(data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return all_folders

    def walk_folder(self, folder_id: str, path: str = "") -> List[Dict]:
        results: List[Dict] = []
        for f in self.list_files_in_folder(folder_id):
            if f["mimeType"] != "application/vnd.google-apps.folder":
                f["path"] = path
                results.append(f)
        for sub in self.list_subfolders(folder_id):
            sub_path = f"{path}/{sub['name']}" if path else sub["name"]
            results.extend(self.walk_folder(sub["id"], sub_path))
        return results

    def download_file(self, file_id: str, dest_path: str) -> None:
        resp = requests.get(
            f"{self.DRIVE_API}/{file_id}",
            headers={**self._headers(), "Accept": "application/octet-stream"},
            params={"alt": "media"},
            stream=True,
            timeout=(15, 600),
        )
        if self._refresh_token_if_needed(resp):
            resp = requests.get(
                f"{self.DRIVE_API}/{file_id}",
                headers={**self._headers(), "Accept": "application/octet-stream"},
                params={"alt": "media"},
                stream=True,
                timeout=(15, 600),
            )
        resp.raise_for_status()

        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  Downloading: {pct}% ({downloaded // 1024 // 1024}MB)", end="", flush=True)
        print()

    def verify_file(self, file_id: str, expected_size: int = 0) -> bool:
        try:
            resp = self._request(
                "get", f"{self.DRIVE_API}/{file_id}",
                params={"fields": "id,name,size"},
            )
        except requests.HTTPError:
            return False
        if expected_size:
            drive_size = int(resp.json().get("size", 0))
            if abs(drive_size - expected_size) > 1024:
                return False
        return True


# ---------------------------------------------------------------------------
# Synology NAS client (File Station API via QuickConnect)
# ---------------------------------------------------------------------------


class SynologyClient:
    QC_RESOLVE_URL = "https://global.quickconnect.to/Serv.php"

    def __init__(self, quickconnect_id: str, username: str, password: str,
                 base_url: Optional[str] = None) -> None:
        self.qc_id = quickconnect_id
        self.username = username
        self.password = password
        self.base_url = base_url
        self.sid: Optional[str] = None

    def resolve_quickconnect(self) -> str:
        if self.base_url:
            return self.base_url

        print(f"Resolving QuickConnect ID '{self.qc_id}'...")
        payload = {
            "version": 1,
            "command": "get_server_info",
            "stop_when_error": "false",
            "stop_when_success": "false",
            "id": "dsm_portal_https",
            "serverID": self.qc_id,
        }
        resp = requests.post(self.QC_RESOLVE_URL, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("errno", -1) != 0:
            print(f"Error: QuickConnect resolution failed: {data}", file=sys.stderr)
            sys.exit(1)

        service = data.get("service", {})
        server = data.get("server", {})
        smartdns = data.get("smartdns", {})

        # Build candidate URLs — direct connections first, relay last.
        # The relay can't handle large file uploads reliably.
        candidates = []

        # 1. Direct external IP (best for uploads — no proxy)
        ext_ip = server.get("external", {}).get("ip")
        svc_port = service.get("port", 5001)
        if ext_ip and ext_ip not in ("::", "0.0.0.0"):
            candidates.append(f"https://{ext_ip}:{svc_port}")

        # 2. LAN IP (if running on the same network)
        for iface in server.get("interface", []):
            lan_ip = iface.get("ip")
            if lan_ip and not lan_ip.startswith("169.254."):
                candidates.append(f"https://{lan_ip}:{svc_port}")

        # 3. SmartDNS external (Synology DNS, may route direct)
        ext_host = smartdns.get("external")
        if ext_host:
            candidates.append(f"https://{ext_host}")

        # 4. Relay — last resort, unreliable for large transfers
        relay = service.get("relay_dn")
        relay_port = service.get("relay_port", 443)
        if relay:
            candidates.append(f"https://{relay}:{relay_port}")

        # Try each candidate with a quick ping
        for url in candidates:
            print(f"  Trying {url}...")
            try:
                r = requests.get(
                    f"{url}/webapi/query.cgi",
                    params={"api": "SYNO.API.Info", "version": 1, "method": "query", "query": "SYNO.API.Auth"},
                    verify=False,
                    timeout=10,
                )
                if r.status_code == 200 and r.json().get("success"):
                    self.base_url = url
                    print(f"  Connected: {url}")
                    return self.base_url
            except (requests.RequestException, ValueError):
                continue

        print("Error: Could not connect to NAS via any QuickConnect endpoint.", file=sys.stderr)
        sys.exit(1)
        return ""

    def login(self) -> None:
        if self.sid:
            return
        base = self.resolve_quickconnect()
        resp = requests.get(
            f"{base}/webapi/auth.cgi",
            params={
                "api": "SYNO.API.Auth",
                "version": 6,
                "method": "login",
                "account": self.username,
                "passwd": self.password,
                "session": "ColdStorage",
                "format": "sid",
            },
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            error_code = data.get("error", {}).get("code", "?")
            print(f"Error: Synology login failed (code {error_code})", file=sys.stderr)
            sys.exit(1)
        self.sid = data["data"]["sid"]
        print("  Synology login successful")

    def logout(self) -> None:
        if not self.sid:
            return
        try:
            requests.get(
                f"{self.base_url}/webapi/auth.cgi",
                params={
                    "api": "SYNO.API.Auth",
                    "version": 6,
                    "method": "logout",
                    "session": "ColdStorage",
                    "_sid": self.sid,
                },
                verify=False,
                timeout=10,
            )
        except Exception:
            pass
        self.sid = None

    def _api_params(self, **extra) -> Dict:
        params = {"_sid": self.sid}
        params.update(extra)
        return params

    def create_folder(self, folder_path: str, name: str) -> bool:
        resp = requests.get(
            f"{self.base_url}/webapi/entry.cgi",
            params=self._api_params(
                api="SYNO.FileStation.CreateFolder",
                version=2,
                method="create",
                folder_path=f'["{folder_path}"]',
                name=f'["{name}"]',
            ),
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            code = data.get("error", {}).get("code", 0)
            if code == 1100:
                return True  # already exists
            print(f"  WARNING: create_folder failed: {data}", file=sys.stderr)
            return False
        return True

    def ensure_folder_path(self, path: str) -> bool:
        parts = path.strip("/").split("/")
        current = ""
        for part in parts:
            parent = f"/{current}" if current else "/"
            current = f"{current}/{part}" if current else part
            if not self.create_folder(parent, part):
                return False
        return True

    def upload_file(self, local_path: str, dest_folder: str) -> bool:
        filename = os.path.basename(local_path)

        with open(local_path, "rb") as f:
            resp = requests.post(
                f"{self.base_url}/webapi/entry.cgi",
                params={"_sid": self.sid},
                data={
                    "api": "SYNO.FileStation.Upload",
                    "version": 2,
                    "method": "upload",
                    "path": dest_folder,
                    "create_parents": "true",
                    "overwrite": "true",
                },
                files={"file": (filename, f, "application/octet-stream")},
                verify=False,
                timeout=600,
            )

        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            print(f"  ERROR: upload failed: {data}", file=sys.stderr)
            return False

        print(f"  Uploaded to NAS: {dest_folder}/{filename}")
        return True

    def get_file_info(self, file_path: str) -> Optional[Dict]:
        resp = requests.get(
            f"{self.base_url}/webapi/entry.cgi",
            params=self._api_params(
                api="SYNO.FileStation.List",
                version=2,
                method="getinfo",
                path=f'["{file_path}"]',
                additional='["size"]',
            ),
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            return None
        files = data.get("data", {}).get("files", [])
        return files[0] if files else None

    def verify_file(self, remote_path: str, expected_size: int) -> bool:
        info = self.get_file_info(remote_path)
        if not info:
            print(f"  File not found on NAS: {remote_path}", file=sys.stderr)
            return False
        nas_size = info.get("additional", {}).get("size", info.get("size", 0))
        if isinstance(nas_size, str):
            nas_size = int(nas_size)
        if expected_size and abs(nas_size - expected_size) > 1024:
            print(
                f"  Size mismatch on NAS: expected {expected_size}, got {nas_size}",
                file=sys.stderr,
            )
            return False
        return True


# ---------------------------------------------------------------------------
# State manager
# ---------------------------------------------------------------------------


class ColdStorageState:
    def __init__(self, path: str = STATE_FILE) -> None:
        self.path = path
        self.state: Dict[str, Dict] = {}
        self.load()

    def load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.state = json.load(f)

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def is_done(self, file_id: str) -> bool:
        entry = self.state.get(file_id)
        return entry is not None and entry.get("status") in ("transferred", "deleted")

    def mark(self, file_id: str, status: str, **metadata) -> None:
        if file_id not in self.state:
            self.state[file_id] = {}
        self.state[file_id]["status"] = status
        self.state[file_id].update(metadata)
        self.save()


# ---------------------------------------------------------------------------
# Mode handlers
# ---------------------------------------------------------------------------


def subpath_for_file(drive_path: str) -> str:
    return drive_path if drive_path else "unsorted"


def mode_scan(drive: GoogleDriveClient, months: int) -> List[Dict]:
    print(f"\nScanning '{DRIVE_FOLDER}' for files older than {months} months...\n")

    root_id = DRIVE_FOLDER_ID
    if not drive.verify_folder(root_id):
        print(f"Error: Drive folder '{DRIVE_FOLDER}' ({root_id}) not accessible.", file=sys.stderr)
        sys.exit(1)

    all_files = drive.walk_folder(root_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)

    old_files = []
    for f in all_files:
        created = f.get("createdTime", "")
        if not created:
            continue
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if dt < cutoff:
            old_files.append(f)

    old_files.sort(key=lambda f: f.get("createdTime", ""))

    total_size = sum(int(f.get("size", 0)) for f in old_files)
    print(f"Found {len(old_files)} files older than {months} months")
    print(f"Total size: {total_size / 1024 / 1024 / 1024:.2f} GB")
    if old_files:
        print(f"Oldest: {old_files[0].get('createdTime', 'N/A')[:10]}")
        print(f"Newest: {old_files[-1].get('createdTime', 'N/A')[:10]}")

    print(f"\n{'Name':<50} {'Path':<25} {'Created':<12} {'Size (MB)':>10}")
    print("-" * 100)
    for f in old_files:
        name = f.get("name", "?")[:49]
        path = f.get("path", "")[:24]
        created = f.get("createdTime", "")[:10]
        size_mb = int(f.get("size", 0)) / 1024 / 1024
        print(f"{name:<50} {path:<25} {created:<12} {size_mb:>9.1f}")

    with open(SCAN_FILE, "w", encoding="utf-8") as fh:
        json.dump(old_files, fh, indent=2, ensure_ascii=False)
    print(f"\nSaved manifest to {SCAN_FILE}")

    return old_files


def mode_transfer(
    drive: GoogleDriveClient,
    nas: SynologyClient,
    state: ColdStorageState,
    months: int,
    output_dir: str,
    nas_base_path: str,
    dry_run: bool,
) -> None:
    if os.path.exists(SCAN_FILE):
        with open(SCAN_FILE, "r", encoding="utf-8") as f:
            old_files = json.load(f)
        print(f"Loaded {len(old_files)} files from {SCAN_FILE}")
    else:
        old_files = mode_scan(drive, months)

    to_transfer = [f for f in old_files if not state.is_done(f["id"])]
    print(f"\n{len(to_transfer)} files to transfer ({len(old_files) - len(to_transfer)} already done)")

    if not to_transfer:
        print("Nothing to transfer.")
        return

    if dry_run:
        print("\n[DRY RUN] Would transfer:")
        for f in to_transfer:
            subpath = subpath_for_file(f.get("path", ""))
            print(f"  {f['name']} → NAS:{nas_base_path}/{subpath}/")
        return

    os.makedirs(output_dir, exist_ok=True)
    nas.login()

    transferred = 0
    for i, item in enumerate(to_transfer, 1):
        file_id = item["id"]
        name = item.get("name", "unknown")
        size = int(item.get("size", 0))
        drive_path = item.get("path", "")
        subpath = subpath_for_file(drive_path)
        dest_folder = f"{nas_base_path}/{subpath}"

        print(f"\n[{i}/{len(to_transfer)}] {name}")
        print(f"  Drive: {DRIVE_FOLDER}/{drive_path}/{name}")
        print(f"  NAS:   {dest_folder}/")

        local_path = os.path.join(output_dir, name)

        try:
            print(f"  Downloading from Drive...")
            drive.download_file(file_id, local_path)

            local_size = os.path.getsize(local_path)
            if size and abs(local_size - size) > 1024:
                print(f"  WARNING: Download size mismatch (expected {size}, got {local_size})", file=sys.stderr)
                os.remove(local_path)
                continue

            print(f"  Creating NAS folder: {dest_folder}")
            nas.ensure_folder_path(dest_folder.lstrip("/"))

            print(f"  Uploading to NAS...")
            if not nas.upload_file(local_path, dest_folder):
                print(f"  ERROR: Upload to NAS failed, skipping", file=sys.stderr)
                os.remove(local_path)
                continue

            remote_file_path = f"{dest_folder}/{name}"
            print(f"  Verifying on NAS...")
            if not nas.verify_file(remote_file_path, local_size):
                print(f"  ERROR: Verification on NAS failed, skipping", file=sys.stderr)
                os.remove(local_path)
                continue

            print(f"  Verified on NAS ({local_size} bytes)")

            state.mark(
                file_id,
                "transferred",
                name=name,
                drive_path=drive_path,
                nas_path=remote_file_path,
                size=size,
                transferred_at=datetime.now(timezone.utc).isoformat(),
            )

            os.remove(local_path)
            transferred += 1

        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            if os.path.exists(local_path):
                os.remove(local_path)
            continue

    print(f"\nTransferred {transferred}/{len(to_transfer)} files to NAS")


def mode_cleanup(state: ColdStorageState) -> None:
    transferred = {
        fid: entry
        for fid, entry in state.state.items()
        if entry.get("status") == "transferred"
    }

    if not transferred:
        print("No transferred files to clean up.")
        return

    # Find the newest file date to use as the cutoff
    dates = []
    for entry in transferred.values():
        created = entry.get("created") or entry.get("transferred_at", "")
        if created:
            dates.append(created[:10])
    cutoff = max(dates) if dates else "unknown"

    print(f"\n{len(transferred)} files transferred to NAS.")
    print(f"\nTo free up Drive space, delete the originals manually in Google Drive:")
    print(f"")
    print(f"  1. Open: https://drive.google.com/drive/folders/{DRIVE_FOLDER_ID}")
    print(f"  2. Click the search filter icon (▼) in the search bar")
    print(f"  3. Set Type: Audio")
    print(f"  4. Set Date modified: Before {cutoff}")
    print(f"  5. Select all matching files and move to trash")
    print(f"")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Move old archive files from Google Drive to DS214play NAS. "
        "With no flags, runs a full scan → transfer → cleanup.",
    )
    parser.add_argument("--scan", action="store_true", help="List archive files older than --months")
    parser.add_argument("--transfer", action="store_true", help="Download from Drive, upload to NAS, verify")
    parser.add_argument("--cleanup", action="store_true", help="Print instructions to delete originals from Google Drive")
    parser.add_argument("--months", type=int, default=12, help="Age threshold in months (default: 12)")
    parser.add_argument("--output", default="./cold-storage-tmp", help="Temp download directory")
    parser.add_argument("--nas-path", default=NAS_BASE_PATH, help=f"NAS destination path (default: {NAS_BASE_PATH})")
    parser.add_argument("--nas-url", default=None, help="Direct NAS URL, skip QuickConnect (e.g. https://192.168.1.29:5001)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    args = parser.parse_args()

    run_all = not (args.scan or args.transfer or args.cleanup)
    if run_all:
        args.scan = True
        args.transfer = True
        args.cleanup = True
        for stale in (SCAN_FILE, STATE_FILE):
            if os.path.exists(stale):
                os.remove(stale)
                print(f"Removed stale state file: {stale}")
        print("No mode specified — running full scan → transfer → cleanup instructions\n")

    nas_user = os.getenv("NAS_USER", "")
    nas_password = os.getenv("NAS_PASSWORD", "")
    if (args.transfer or run_all) and (not nas_user or not nas_password):
        print(
            "Error: NAS_USER and NAS_PASSWORD must be set in .env for transfer mode",
            file=sys.stderr,
        )
        sys.exit(1)

    drive = GoogleDriveClient()
    state = ColdStorageState()
    nas = SynologyClient(QUICKCONNECT_ID, nas_user, nas_password, base_url=args.nas_url)

    if args.scan:
        mode_scan(drive, args.months)

    if args.transfer:
        mode_transfer(drive, nas, state, args.months, args.output, args.nas_path, args.dry_run)

    if args.cleanup:
        mode_cleanup(state)

    nas.logout()


if __name__ == "__main__":
    main()

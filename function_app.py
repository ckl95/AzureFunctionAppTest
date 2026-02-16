
import azure.functions as func
import logging
import os
import json
import re
import time
import base64
import pandas as pd
from typing import Any, Dict, List
from collections import defaultdict

from azure.identity import DefaultAzureCredential
from azure.core.credentials import AccessToken, TokenCredential
from azure.core.exceptions import HttpResponseError, ClientAuthenticationError
from azure.storage.filedatalake import DataLakeServiceClient, FileSystemClient


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# ---------- Small helpers for bearer pass-through ----------
class StaticTokenCredential(TokenCredential):
    """Wrap a raw access token so Azure SDKs can use it."""
    def __init__(self, token: str, expires_in: int = 3600):
        self._token = token
        self._exp = int(time.time()) + expires_in

    def get_token(self, *scopes, **kwargs) -> AccessToken:
        return AccessToken(self._token, self._exp)


def _get_bearer(req: func.HttpRequest) -> str | None:
    h = req.headers.get("Authorization") or req.headers.get("authorization")
    if not h:
        return None
    parts = h.split()
    return parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else None


def _jwt_aud(token: str) -> str | None:
    """Decode JWT payload (no signature validation) and return 'aud' if present."""
    try:
        payload_b64 = token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8"))
        return payload.get("aud")
    except Exception:
        return None


def make_fs_client(account_url: str, filesystem: str, req: func.HttpRequest, body: dict) -> FileSystemClient:
    """
    Choose credential in this order:
      1) SAS (body 'sas' or header 'x-ms-storage-sas')
      2) Bearer in Authorization header ONLY if audience == storage.azure.com
      3) Managed Identity / DefaultAzureCredential
    """
    # 1) SAS
    sas = body.get("sas") or req.headers.get("x-ms-storage-sas")
    if sas:
        svc = DataLakeServiceClient(account_url=f"{account_url}?{sas}")
        return svc.get_file_system_client(filesystem)

    # 2) Bearer pass-through (must be storage-scoped)
    token = _get_bearer(req)
    if token:
        aud = _jwt_aud(token) or ""
        if "storage.azure.com" in aud:
            cred = StaticTokenCredential(token)
            svc = DataLakeServiceClient(account_url=account_url, credential=cred)
            return svc.get_file_system_client(filesystem)
        else:
            logging.info("Authorization header present but audience != storage.azure.com; falling back to Managed Identity.")

    # 3) Managed Identity / default chain
    credential = DefaultAzureCredential(exclude_visual_studio_code_credential=False)
    svc = DataLakeServiceClient(account_url=account_url, credential=credential)
    return svc.get_file_system_client(filesystem)


# ----------------- Your function -----------------
@app.route(route="http_trigger", methods=["POST"])
def http_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("HTTP trigger: ADLS JSON → normalized preview")

    # ---- Parse request body ----
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid or missing JSON body.", status_code=400)

    # ---- Inputs (moved to env with sane defaults) ----
    ADLS_ACCOUNT_URL = os.environ.get(
        "ADLS_ACCOUNT_URL",
        "https://bzowedstfdatalake01.dfs.core.windows.net"   # NOTE: dfs endpoint here
    )
    ADLS_FILESYSTEM = os.environ.get("ADLS_FILESYSTEM", "raw")

    subfolder = body.get("subfolder")
    if not subfolder:
        return func.HttpResponse("Missing 'subfolder' in POST body.", status_code=400)

    # ---------- Helpers ----------
    def flatten_json(obj: Any, parent: str = "", sep: str = ".") -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = f"{parent}{sep}{k}" if parent else k
                out.update(flatten_json(v, key, sep))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                key = f"{parent}[{i}]"
                out.update(flatten_json(v, key, sep))
        else:
            out[parent] = obj
        return out

    def to_days_table(nested: Dict[str, Any], remove_indices: bool = True) -> pd.DataFrame:
        flat = flatten_json(nested)
        pat = re.compile(r'days\[(\d+)\]\.?(.*)$', re.IGNORECASE)

        rows: Dict[int, Dict[str, Any]] = defaultdict(dict)
        meta: Dict[str, Any] = {}

        for k, v in flat.items():
            m = pat.search(k.strip())
            if m:
                day_idx = int(m.group(1))
                suffix = m.group(2) or "value"
                if remove_indices:
                    suffix = re.sub(r'\[\d+\]', '', suffix)
                rows[day_idx][suffix] = v
            else:
                meta[k] = v

        df = pd.DataFrame.from_dict(rows, orient="index").sort_index().reset_index()
        df = df.rename(columns={"index": "day"})

        if "rosterDate" in df.columns:
            df = df[["day", "rosterDate"] + [c for c in df.columns if c not in ("day", "rosterDate")]]

        for k, v in meta.items():
            df[k] = v

        return df

    # ---- Create filesystem client (now supports SAS / bearer / MI) ----
    try:
        fs_client = make_fs_client(ADLS_ACCOUNT_URL, ADLS_FILESYSTEM, req, body)
    except Exception as ex:
        logging.exception("Failed to create ADLS filesystem client.")
        return func.HttpResponse(f"ADLS client error: {ex}", status_code=500)

    # ---- Enumerate JSON files in subfolder ----
    json_files: List[str] = []
    try:
        for item in fs_client.get_paths(path=subfolder):
            if (not item.is_directory) and item.name.lower().endswith(".json"):
                json_files.append(item.name)
    except ClientAuthenticationError as ex:
        logging.exception("Authentication to ADLS failed.")
        return func.HttpResponse(f"ADLS auth error: {ex}", status_code=401)
    except HttpResponseError as ex:
        if getattr(ex, "status_code", None) == 403:
            return func.HttpResponse(
                "ADLS authorization error (403). Verify RBAC role and ACLs (execute on parents, r-x on target).",
                status_code=403
            )
        logging.exception("Failed to list paths from ADLS.")
        return func.HttpResponse(f"ADLS list error: {ex}", status_code=500)
    except Exception as ex:
        logging.exception("Failed to list paths from ADLS (unexpected).")
        return func.HttpResponse(f"ADLS list error: {ex}", status_code=500)

    if not json_files:
        return func.HttpResponse(
            json.dumps({"subfolder": subfolder, "files_found": [], "message": "No JSON files found."}),
            status_code=200,
            mimetype="application/json"
        )

    # ---- Download, parse, and tabularize ----
    frames: List[pd.DataFrame] = []
    errors: List[Dict[str, str]] = []

    for path in json_files:
        try:
            fc = fs_client.get_file_client(path)
            data = fc.download_file().readall().decode("utf-8")
            obj = json.loads(data)
            df = to_days_table(obj, remove_indices=True)
            df["__source_path"] = path
            frames.append(df)
        except Exception as ex:
            logging.exception("Failed to process file: %s", path)
            errors.append({"path": path, "error": str(ex)})

    if frames:
        combined = pd.concat(frames, ignore_index=True)
        payload = {
            "subfolder": subfolder,
            "files_found": json_files,
            "rows": int(combined.shape[0]),
            "columns": list(combined.columns),
            "preview": combined.head(10).to_dict(orient="records"),
            "errors": errors
        }
    else:
        payload = {
            "subfolder": subfolder,
            "files_found": json_files,
            "rows": 0,
            "columns": [],
            "preview": [],
            "errors": errors
        }

    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False, indent=2),
        status_code=200,
        mimetype="application/json"
    )

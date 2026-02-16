import azure.functions as func
import logging
import os
import json
import re
import pandas as pd
from typing import Any, Dict, List
from collections import defaultdict
from azure.identity import DefaultAzureCredential
from azure.storage.filedatalake import DataLakeServiceClient, FileSystemClient

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
 
 
@app.route(route="http_trigger", methods=["POST"])
def http_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Python HTTP trigger function processed a request (normal → normalized JSON array)")
 
    # ---- Parse request body ----
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid or missing JSON body.", status_code=400)
 
    # ---------- Helpers ----------
    def flatten_json(obj: Any, parent: str = "", sep: str = ".") -> Dict[str, Any]:
        """Flatten nested dict/list into dot/bracket path keys."""
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
        """
        Convert flattened JSON into a table with:
        - one row per days[n]
        - columns = suffix after 'days[n].'
        If remove_indices=True:
        'parts[0].shift.uname' → 'parts.shift.uname'
        """
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
                meta[k] = v  # top-level keys
 
        df = pd.DataFrame.from_dict(rows, orient="index").sort_index().reset_index()
        df = df.rename(columns={"index": "day"})
 
        # Move rosterDate to the front if present
        if "rosterDate" in df.columns:
            df = df[["day", "rosterDate"] + [c for c in df.columns if c not in ("day", "rosterDate")]]
 
        # Add top-level fields to each row
        for k, v in meta.items():
            df[k] = v
 
        return df
 
    def get_fs_client(account_url: str, filesystem: str) -> "FileSystemClient":
        """
        Build an ADLS Gen2 FileSystemClient using DefaultAzureCredential.
        Works with Managed Identity in Azure and dev identity locally.
        """
        credential = DefaultAzureCredential()
        svc = DataLakeServiceClient(account_url=account_url, credential=credential)
        return svc.get_file_system_client(filesystem=filesystem)
 
    # ---- Inputs ----
    ADLS_FILESYSTEM='raw'
    ADLS_ACCOUNT_URL = 'https://adls01testcasperklaver.blob.core.windows.net/'

    subfolder = body.get("subfolder")
    if not subfolder:
        return func.HttpResponse("Missing 'subfolder' in POST body.", status_code=400)
 
    # ---- Read env settings ----
    try:
        account_url = ADLS_ACCOUNT_URL  # e.g., "https://<acct>.dfs.core.windows.net"
        filesystem = ADLS_FILESYSTEM    # e.g., "raw"
    except KeyError as ex:
        return func.HttpResponse(f"Missing environment variable: {ex}", status_code=500)
 
    # ---- Create filesystem client ----
    try:
        fs_client = get_fs_client(account_url, filesystem)
    except Exception as ex:
        logging.exception("Failed to create ADLS filesystem client.")
        return func.HttpResponse(f"ADLS client error: {ex}", status_code=500)
    payload = [{"status": "succeed"}]
 
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False, indent=2),
        status_code=200,
        mimetype="application/json"
    )
 




import azure.functions as func
import logging
import os
import json
import re
import pandas as pd
from typing import Any, Dict, List
from collections import defaultdict
from azure.identity import DefaultAzureCredential
from azure.storage.filedatalake import DataLakeServiceClient


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="http_trigger", methods=["POST"])
def http_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Python HTTP trigger function processed a request (Graph → normalized JSON array)")
    
    try:
        name = req.params.get('name')
        return func.HttpResponse(f"Hello, {name}. This HTTP triggered function executed successfully.")   
    except:
        try:
            req_body = req.get_json()
            return func.HttpResponse(
                json.dumps(req_body, ensure_ascii=False),
                mimetype="application/json",
                status_code=200
            )    
        except ValueError:
            pass
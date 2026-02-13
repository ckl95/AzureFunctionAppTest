
import azure.functions as func
import logging
import os
import json
import re
from typing import Any, Dict, List
from collections import defaultdict
import pandas as pd
from azure.identity import DefaultAzureCredential
from azure.storage.filedatalake import DataLakeServiceClient


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="http_trigger", methods=["POST"])
def http_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Python HTTP trigger function processed a request (Graph → normalized JSON array)")

    foobar = 'a'

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid or missing JSON body.", status_code=400)
    return func.HttpResponse(
        json.dumps(body, ensure_ascii=False),
        mimetype="application/json",
        status_code=200
    )



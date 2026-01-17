import azure.functions as func
import logging
import json
import urllib.request
import urllib.parse

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="http_trigger", methods=["POST"])
def http_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Python HTTP trigger function processed a request (Graph → normalized JSON array)")

    all_rows = []

    all_rows.append({"yolo":"Zo ouderwets"})

    # Return ONLY the array of nice flat rows
    return func.HttpResponse(
        json.dumps(all_rows, ensure_ascii=False),
        mimetype="application/json",
        status_code=200
    )

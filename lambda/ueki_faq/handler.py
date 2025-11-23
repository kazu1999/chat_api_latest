import json
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError


AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-1")
FAQ_TABLE_NAME = os.getenv("FAQ_TABLE_NAME", "ueki-faq")

_ddb = boto3.resource("dynamodb", region_name=AWS_REGION)
_table = _ddb.Table(FAQ_TABLE_NAME)


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resp(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def handler(event, context):
    try:
        http = event.get("requestContext", {}).get("http", {})
        method = http.get("method", "GET").upper()
        path = http.get("path", "/")

        if method == "OPTIONS":
            return _resp(200, {"ok": True})

        if method == "GET" and path.startswith("/faqs"):
            # list
            resp = _table.scan(Limit=200)
            return _resp(200, {"ok": True, "items": resp.get("Items", [])})

        if path.startswith("/faq"):
            # Path params
            question = None
            parts = path.split("/", 2)
            if len(parts) == 3 and parts[2]:
                question = parts[2]

            if method == "POST" and path == "/faq":
                body = json.loads(event.get("body") or "{}")
                q = body.get("question")
                a = body.get("answer")
                if not q or a is None:
                    return _resp(400, {"ok": False, "error": "question and answer required"})
                item = {"question": q, "answer": a, "created_at": _now_iso(), "updated_at": _now_iso()}
                _table.put_item(Item=item, ConditionExpression="attribute_not_exists(question)")
                return _resp(200, {"ok": True, "item": item})

            if method == "GET" and question:
                r = _table.get_item(Key={"question": question})
                item = r.get("Item")
                if not item:
                    return _resp(404, {"ok": False, "error": "not found"})
                return _resp(200, {"ok": True, "item": item})

            if method == "PUT" and question:
                body = json.loads(event.get("body") or "{}")
                a = body.get("answer")
                if a is None:
                    return _resp(400, {"ok": False, "error": "answer required"})
                r = _table.update_item(
                    Key={"question": question},
                    UpdateExpression="SET answer = :a, updated_at = :u",
                    ExpressionAttributeValues={":a": a, ":u": _now_iso()},
                    ConditionExpression="attribute_exists(question)",
                    ReturnValues="ALL_NEW",
                )
                return _resp(200, {"ok": True, "item": r.get("Attributes")})

            if method == "DELETE" and question:
                _table.delete_item(Key={"question": question}, ConditionExpression="attribute_exists(question)")
                return _resp(200, {"ok": True})

        return _resp(404, {"ok": False, "error": "route not found"})

    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        status = 409 if code in ("ConditionalCheckFailedException",) else 500
        return _resp(status, {"ok": False, "error": str(e)})
    except (BotoCoreError, Exception) as e:
        return _resp(500, {"ok": False, "error": str(e)})



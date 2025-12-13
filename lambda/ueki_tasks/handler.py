import json
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
import auth

TABLE_NAME = os.getenv("TASKS_TABLE_NAME", "ueki-tasks")
_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(TABLE_NAME)

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
        client_id = auth.get_client_id(event)
        
        http = event.get("requestContext", {}).get("http", {})
        method = http.get("method", "GET").upper()
        path = http.get("path", "/")

        if method == "OPTIONS":
            return _resp(200, {"ok": True})

        # List tasks
        if method == "GET" and path == "/tasks":
            # Use Query instead of Scan for tenant isolation
            from boto3.dynamodb.conditions import Key
            r = _table.query(
                KeyConditionExpression=Key("client_id").eq(client_id),
                Limit=200
            )
            return _resp(200, {"ok": True, "items": r.get("Items", [])})

        if path.startswith("/task"):
            name = None
            parts = path.split("/", 2)
            if len(parts) == 3 and parts[2]:
                name = parts[2]

            if method == "POST" and path == "/task":
                body = json.loads(event.get("body") or "{}")
                nm = body.get("name")
                phone = body.get("phone_number") or body.get("phone") or ""
                address = body.get("address") or ""
                start = body.get("start_datetime") or body.get("start_date") or ""
                req = body.get("request") or body.get("requirement") or ""
                if not nm:
                    return _resp(400, {"ok": False, "error": "name required"})
                item = {
                    "client_id": client_id,
                    "name": nm,
                    "phone_number": phone,
                    "address": address,
                    "request": req,
                    "start_datetime": start,
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                }
                _table.put_item(
                    Item=item,
                    ConditionExpression="attribute_not_exists(#n)",
                    ExpressionAttributeNames={"#n": "name"},
                )
                return _resp(200, {"ok": True, "item": item})

            if method == "GET" and name:
                r = _table.get_item(Key={"client_id": client_id, "name": name})
                it = r.get("Item")
                if not it:
                    return _resp(404, {"ok": False, "error": "not found"})
                return _resp(200, {"ok": True, "item": it})

            if method == "PUT" and name:
                body = json.loads(event.get("body") or "{}")
                expr_parts = []
                expr_attr_values = {":updated_at": _now_iso()}
                expr_attr_names = {"#n": "name", "#updated_at": "updated_at"}

                # Accept new fields and legacy aliases
                updates = {
                    "request": body.get("request") if "request" in body else body.get("requirement"),
                    "start_datetime": body.get("start_datetime") if "start_datetime" in body else body.get("start_date"),
                    "phone_number": body.get("phone_number") if "phone_number" in body else body.get("phone"),
                    "address": body.get("address") if "address" in body else None,
                }

                for field, value in updates.items():
                    if value is None:
                        continue
                    alias = f"#{field}"
                    valkey = f":{field}"
                    expr_parts.append(f"{alias} = {valkey}")
                    expr_attr_names[alias] = field
                    expr_attr_values[valkey] = value

                if not expr_parts:
                    return _resp(400, {"ok": False, "error": "nothing to update"})

                expr_parts.append("#updated_at = :updated_at")

                r = _table.update_item(
                    Key={"client_id": client_id, "name": name},
                    UpdateExpression="SET " + ", ".join(expr_parts),
                    ExpressionAttributeValues=expr_attr_values,
                    ExpressionAttributeNames=expr_attr_names,
                    ConditionExpression="attribute_exists(#n)",
                    ReturnValues="ALL_NEW",
                )
                return _resp(200, {"ok": True, "item": r.get("Attributes")})

            if method == "DELETE" and name:
                _table.delete_item(
                    Key={"client_id": client_id, "name": name},
                    ConditionExpression="attribute_exists(#n)",
                    ExpressionAttributeNames={"#n": "name"},
                )
                return _resp(200, {"ok": True})

        return _resp(404, {"ok": False, "error": "route not found"})

    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        status = 409 if code in ("ConditionalCheckFailedException",) else 500
        return _resp(status, {"ok": False, "error": str(e)})
    except (BotoCoreError, Exception) as e:
        return _resp(500, {"ok": False, "error": str(e)})

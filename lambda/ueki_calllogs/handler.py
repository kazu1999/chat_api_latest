import base64
import json
import os
from datetime import datetime, timezone
from urllib import request as _urlreq
from urllib import error as _urlerr
from urllib.parse import urlencode as _urlencode
import tempfile
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError


CALLS_TABLE_NAME = os.getenv("CALL_LOGS_TABLE_NAME", "ueki-chatbot")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID") or ""
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN") or ""
OPENAI_SECRET_NAME = os.getenv("OPENAI_SECRET_NAME") or "UEKI_OPENAI_APIKEY"
OPENAI_API_KEY_ENV = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_APIKEY") or os.getenv("OPENAI_API_TOKEN")
OPENAI_PROJECT_ENV = os.getenv("OPENAI_PROJECT") or os.getenv("OPENAI_PROJECT_ID")
OPENAI_ORG_ENV = os.getenv("OPENAI_ORG") or os.getenv("OPENAI_ORGANIZATION")
_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(CALLS_TABLE_NAME)
_session = boto3.session.Session()
_secrets = _session.client("secretsmanager", region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-northeast-1")
_OPENAI_API_KEY_CACHE: Optional[str] = None
_OPENAI_PROJECT_ID_CACHE: Optional[str] = None
_OPENAI_ORG_CACHE: Optional[str] = None


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


def _bin_resp(status: int, content_type: str, data: bytes):
    return {
        "statusCode": status,
        "headers": {
            "content-type": content_type,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
            # Suggest a browser filename for downloads (optional)
            # "Content-Disposition": "inline",
        },
        "isBase64Encoded": True,
        "body": base64.b64encode(data).decode("ascii"),
    }


def _digits_only(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit() or ch == '+')


def _normalize_phone_number(raw: str | None) -> str | None:
    if not raw:
        return raw
    s = _digits_only(str(raw))
    if s.startswith('+81') and len(s) >= 4:
        rest = s[3:]
        if not rest.startswith('0'):
            return '0' + rest
        return rest
    if s.startswith('+'):
        return s[1:]
    return s


def _parse_query(event):
    return event.get("queryStringParameters") or {}


def _twilio_auth_header() -> str:
    token = f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode("utf-8")
    return "Basic " + base64.b64encode(token).decode("ascii")


def _http_get_bytes(url: str, headers: dict[str, str] | None = None, timeout_secs: int = 15) -> bytes:
    req = _urlreq.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with _urlreq.urlopen(req, timeout=timeout_secs) as resp:
        return resp.read()


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    raw = _http_get_bytes(url, headers=headers)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _get_openai_api_key() -> Optional[str]:
    global _OPENAI_API_KEY_CACHE, _OPENAI_PROJECT_ID_CACHE, _OPENAI_ORG_CACHE
    if _OPENAI_API_KEY_CACHE:
        return _OPENAI_API_KEY_CACHE
    # 1) Prefer Secrets Manager
    try:
        r = _secrets.get_secret_value(SecretId=OPENAI_SECRET_NAME)
        secret = (r.get("SecretString") or "").strip()
        if secret:
            key: Optional[str] = None
            project: Optional[str] = None
            org: Optional[str] = None
            if secret.startswith("{") and secret.endswith("}"):
                try:
                    obj = json.loads(secret)
                    # Broad key search (align with chat handler)
                    for k in ("OPENAI_API_KEY", "api_key", "key", "OPENAI_APIKEY", "openai_api_key", "OPENAI-API-KEY", "API_KEY", "token"):
                        if obj.get(k):
                            key = str(obj[k]).strip()
                            break
                    for k in ("OPENAI_PROJECT", "project", "project_id"):
                        if obj.get(k):
                            project = str(obj[k]).strip()
                            break
                    for k in ("OPENAI_ORG", "OPENAI_ORGANIZATION", "organization", "org"):
                        if obj.get(k):
                            org = str(obj[k]).strip()
                            break
                    # Final fallback: any plausible-looking string value
                    if not key:
                        for v in obj.values():
                            if isinstance(v, str) and (v.startswith("sk-") or len(v) > 20):
                                key = v.strip()
                                break
                except Exception:
                    # Not valid JSON; fallthrough
                    key = None
            if not key:
                # Plain string, or embedded JSON-like string containing sk-
                if secret.startswith("sk-"):
                    key = secret
                else:
                    # Try naive extraction of sk- token
                    try:
                        import re
                        m = re.search(r"(sk-[A-Za-z0-9]{10,})", secret)
                        if m:
                            key = m.group(1).strip()
                    except Exception:
                        pass
                # If still none, treat full secret as key as last resort
                if not key and secret and "{" not in secret:
                    key = secret
            # Set caches if resolved
            if key:
                _OPENAI_API_KEY_CACHE = key.strip()
                _OPENAI_PROJECT_ID_CACHE = (project or _OPENAI_PROJECT_ID_CACHE)
                _OPENAI_ORG_CACHE = (org or _OPENAI_ORG_CACHE)
                return _OPENAI_API_KEY_CACHE
            else:
                print("[ueki-calllogs] OPENAI secret present but no api key field resolved", flush=True)
    except Exception as e:
        print(f"[ueki-calllogs] Failed to fetch OPENAI secret: {e}", flush=True)
    # 2) Fallback to environment variables
    if OPENAI_API_KEY_ENV:
        _OPENAI_API_KEY_CACHE = OPENAI_API_KEY_ENV.strip()
        if OPENAI_PROJECT_ENV:
            _OPENAI_PROJECT_ID_CACHE = OPENAI_PROJECT_ENV.strip()
        if OPENAI_ORG_ENV:
            _OPENAI_ORG_CACHE = OPENAI_ORG_ENV.strip()
        return _OPENAI_API_KEY_CACHE
    return None


def _get_openai_project_id() -> Optional[str]:
    if _OPENAI_PROJECT_ID_CACHE:
        return _OPENAI_PROJECT_ID_CACHE
    # allow env override
    env_project = os.getenv("OPENAI_PROJECT") or os.getenv("OPENAI_PROJECT_ID")
    if env_project:
        return env_project.strip()
    return None


def handler(event, context):
    try:
        http = event.get("requestContext", {}).get("http", {})
        method = http.get("method", "GET").upper()
        path = http.get("path", "/")

        if method == "OPTIONS":
            return _resp(200, {"ok": True})

        # ======= Twilio Recordings: List by call_sid =======
        # GET /recordings?call_sid=CAxxxx
        if method == "GET" and path == "/recordings":
            q = _parse_query(event)
            call_sid = (q.get("call_sid") or q.get("callSid") or "").strip()
            if not call_sid:
                return _resp(400, {"ok": False, "error": "call_sid is required"})
            if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
                return _resp(500, {"ok": False, "error": "Twilio credentials are not configured"})
            url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls/{call_sid}/Recordings.json"
            data = _http_get_json(url, headers={"Authorization": _twilio_auth_header()})
            recs = data.get("recordings") or data.get("recordings", []) or data.get("items") or []
            # Normalize fields we care about
            items = []
            for r in recs:
                items.append({
                    "sid": r.get("sid"),
                    "duration": r.get("duration"),
                    "date_created": r.get("date_created") or r.get("dateCreated"),
                    "media_format": "mp3",
                })
            return _resp(200, {"ok": True, "items": items})

        # ======= Twilio Recording: Stream audio =======
        # GET /recording/{sid}            (defaults to mp3)
        # GET /recording/{sid}.mp3
        if method == "GET" and path.startswith("/recording"):
            if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
                return _resp(500, {"ok": False, "error": "Twilio credentials are not configured"})
            # Extract recording SID from path
            # Accept both /recording/RE123 and /recording/RE123.mp3
            parts = path.split("/")
            sid_part = parts[-1] if parts else ""
            if not sid_part:
                return _resp(400, {"ok": False, "error": "recording sid missing"})
            sid = sid_part.split(".")[0]
            # format optional (default mp3)
            q = _parse_query(event)
            fmt = (q.get("format") or "mp3").lower()
            if fmt not in ("mp3", "wav"):
                fmt = "mp3"
            url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Recordings/{sid}.{fmt}"
            data = _http_get_bytes(url, headers={"Authorization": _twilio_auth_header()})
            ct = "audio/mpeg" if fmt == "mp3" else "audio/wav"
            return _bin_resp(200, ct, data)

        # ======= Transcription via OpenAI Whisper =======
        # GET /transcription?recording_sid=RE...&format=mp3
        if method == "GET" and path == "/transcription":
            q = _parse_query(event)
            rec_sid = (q.get("recording_sid") or q.get("sid") or "").strip()
            fmt = (q.get("format") or "mp3").lower()
            if not rec_sid:
                return _resp(400, {"ok": False, "error": "recording_sid is required"})
            if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
                return _resp(500, {"ok": False, "error": "Twilio credentials are not configured"})
            api_key = _get_openai_api_key()
            if not api_key:
                return _resp(500, {"ok": False, "error": "OpenAI API key is not configured (Secrets Manager)"})
            if fmt not in ("mp3", "wav"):
                fmt = "mp3"
            # fetch audio bytes from Twilio
            url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Recordings/{rec_sid}.{fmt}"
            # fetch Twilio audio with a conservative timeout to keep total under 30s
            audio_bytes = _http_get_bytes(url, headers={"Authorization": _twilio_auth_header()}, timeout_secs=10)
            # write to temp file with extension
            with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as tf:
                tf.write(audio_bytes)
                temp_path = tf.name
            # call OpenAI Whisper via REST (no SDK dependency)
            try:
                boundary = f"----uekiBoundary{int(datetime.now().timestamp()*1000)}"
                def _part_headers(name: str, extra: str = "") -> bytes:
                    return (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"{extra}\r\n\r\n').encode("utf-8")
                body = bytearray()
                # model
                body += _part_headers("model")
                body += b"whisper-1\r\n"
                # response_format
                body += _part_headers("response_format")
                body += b"verbose_json\r\n"
                # temperature
                body += _part_headers("temperature")
                body += b"0\r\n"
                # file
                content_type = "audio/mpeg" if fmt == "mp3" else "audio/wav"
                body += _part_headers("file", f'; filename="audio.{fmt}"')
                body += (f"Content-Type: {content_type}\r\n\r\n").encode("utf-8")
                body += audio_bytes
                body += b"\r\n"
                # end boundary
                body += (f"--{boundary}--\r\n").encode("utf-8")
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                }
                project_id = _get_openai_project_id()
                if project_id:
                    headers["OpenAI-Project"] = project_id
                if _OPENAI_ORG_CACHE:
                    headers["OpenAI-Organization"] = _OPENAI_ORG_CACHE
                req = _urlreq.Request(
                    "https://api.openai.com/v1/audio/transcriptions",
                    method="POST",
                    data=bytes(body),
                    headers=headers,
                )
                try:
                    with _urlreq.urlopen(req, timeout=25) as resp:
                        raw = resp.read()
                except _urlerr.HTTPError as he:
                    try:
                        err_body = he.read().decode("utf-8", errors="ignore")
                    except Exception:
                        err_body = ""
                    return _resp(502, {"ok": False, "error": f"OpenAI HTTP {he.code}: {err_body[:500]}"})
                except _urlerr.URLError as ue:
                    return _resp(502, {"ok": False, "error": f"OpenAI URL error: {ue.reason}"})
                try:
                    result = json.loads(raw.decode("utf-8"))
                except Exception:
                    return _resp(502, {"ok": False, "error": "Invalid response from OpenAI"})
                # If OpenAI returned an error object, surface it
                if isinstance(result, dict) and "error" in result:
                    err = result.get("error") or {}
                    msg = err.get("message") or "OpenAI error"
                    code = err.get("code") or ""
                    return _resp(502, {"ok": False, "error": f"{msg} ({code})"})
                text = result.get("text") or ""
                segments = result.get("segments") or None
                return _resp(200, {"ok": True, "text": text, "segments": segments})
            finally:
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

        # List calls by phone with optional range/paging
        if method == "GET" and path.startswith("/calls"):
            q = _parse_query(event)
            phone = _normalize_phone_number(q.get("phone"))
            if not phone:
                return _resp(400, {"ok": False, "error": "phone is required"})
            ts_from = q.get("from")
            ts_to = q.get("to")
            limit = int(q.get("limit") or 50)
            order = (q.get("order") or "asc").lower()  # 'asc' | 'desc'
            next_token = None
            if "next_token" in q and q["next_token"]:
                try:
                    next_token = json.loads(q["next_token"])  # serialized ExclusiveStartKey
                except Exception:
                    next_token = None

            kwargs = {
                "KeyConditionExpression": Key("phone_number").eq(phone),
                "ScanIndexForward": False if order == "desc" else True,
                "Limit": limit,
            }
            if ts_from and ts_to:
                kwargs["KeyConditionExpression"] = Key("phone_number").eq(phone) & Key("ts").between(ts_from, ts_to)
            elif ts_from:
                kwargs["KeyConditionExpression"] = Key("phone_number").eq(phone) & Key("ts").gte(ts_from)
            elif ts_to:
                kwargs["KeyConditionExpression"] = Key("phone_number").eq(phone) & Key("ts").lte(ts_to)
            if next_token:
                kwargs["ExclusiveStartKey"] = next_token

            r = _table.query(**kwargs)
            return _resp(200, {"ok": True, "items": r.get("Items", []), "next_token": r.get("LastEvaluatedKey")})

        # List distinct phones (scan, dedupe)
        if method == "GET" and path.startswith("/phones"):
            try:
                phones = set()
                last_key = None
                while True:
                    # Scan in small pages to avoid timeouts/large payloads
                    scan_kwargs = {"Limit": 500}
                    if last_key:
                        scan_kwargs["ExclusiveStartKey"] = last_key
                    r = _table.scan(**scan_kwargs)
                    for it in r.get("Items", []):
                        pn = it.get("phone_number")
                        if pn:
                            norm = _normalize_phone_number(pn)
                            if norm:
                                phones.add(norm)
                    last_key = r.get("LastEvaluatedKey")
                    if not last_key or len(phones) >= 1000:
                        break
                return _resp(200, {"ok": True, "items": sorted(list(phones))})
            except Exception as e:
                # Force log to CloudWatch for quick diagnosis
                print(f"[ueki-calllogs] /phones error: {e}", flush=True)
                return _resp(500, {"ok": False, "error": str(e)})

        # Create call log
        if method == "POST" and path == "/call":
            body = json.loads(event.get("body") or "{}")
            phone = _normalize_phone_number(body.get("phone_number"))
            if not phone:
                return _resp(400, {"ok": False, "error": "phone_number required"})
            ts = body.get("ts") or _now_iso()
            user_text = body.get("user_text") or ""
            assistant_text = body.get("assistant_text") or ""
            call_sid = body.get("call_sid") or body.get("callSid") or ""
            item = {
                "phone_number": phone,
                "ts": ts,
                "user_text": user_text,
                "assistant_text": assistant_text,
                **({"call_sid": call_sid} if call_sid else {}),
            }
            _table.put_item(Item=item)
            return _resp(200, {"ok": True, "item": item})

        # Get call log(s)
        if method == "GET" and path == "/call":
            q = _parse_query(event)
            phone = _normalize_phone_number(q.get("phone"))
            ts = q.get("ts")
            call_sid = q.get("call_sid") or q.get("callSid")
            if call_sid and not (phone and ts):
                # Lookup by GSI callSidIndex (return all items for this session)
                items_acc = []
                last_key = None
                while True:
                    kwargs = {
                        "IndexName": "callSidIndex",
                        "KeyConditionExpression": Key("call_sid").eq(call_sid),
                        "Limit": 200,
                    }
                    if last_key:
                        kwargs["ExclusiveStartKey"] = last_key
                    r = _table.query(**kwargs)
                    items_acc.extend(r.get("Items", []))
                    last_key = r.get("LastEvaluatedKey")
                    if not last_key:
                        break
                if not items_acc:
                    return _resp(404, {"ok": False, "error": "not found"})
                return _resp(200, {"ok": True, "items": items_acc})
            if not phone or not ts:
                return _resp(400, {"ok": False, "error": "phone and ts required (or provide call_sid)"})
            r = _table.get_item(Key={"phone_number": phone, "ts": ts})
            it = r.get("Item")
            if not it:
                return _resp(404, {"ok": False, "error": "not found"})
            return _resp(200, {"ok": True, "item": it})

        # Update call log
        if method == "PUT" and path == "/call":
            body = json.loads(event.get("body") or "{}")
            phone = _normalize_phone_number(body.get("phone_number"))
            ts = body.get("ts")
            if not phone or not ts:
                return _resp(400, {"ok": False, "error": "phone_number and ts required"})
            expr = []
            values = {}
            if "user_text" in body:
                expr.append("user_text = :u")
                values[":u"] = body.get("user_text")
            if "assistant_text" in body:
                expr.append("assistant_text = :a")
                values[":a"] = body.get("assistant_text")
            if "call_sid" in body or "callSid" in body:
                expr.append("call_sid = :c")
                values[":c"] = body.get("call_sid") or body.get("callSid")
            if not expr:
                return _resp(400, {"ok": False, "error": "nothing to update"})
            r = _table.update_item(
                Key={"phone_number": phone, "ts": ts},
                UpdateExpression="SET " + ", ".join(expr),
                ExpressionAttributeValues=values,
                ConditionExpression="attribute_exists(phone_number) AND attribute_exists(ts)",
                ReturnValues="ALL_NEW",
            )
            return _resp(200, {"ok": True, "item": r.get("Attributes")})

        # Delete call log(s)
        if method == "DELETE" and path.startswith("/call"):
            q = _parse_query(event)
            phone = _normalize_phone_number(q.get("phone"))
            ts = q.get("ts")
            call_sid = q.get("call_sid") or q.get("callSid")
            if call_sid and not (phone and ts):
                # delete all items by call_sid via GSI scan + per-item delete
                items_acc = []
                last_key = None
                while True:
                    kwargs = {
                        "IndexName": "callSidIndex",
                        "KeyConditionExpression": Key("call_sid").eq(call_sid),
                        "Limit": 200,
                    }
                    if last_key:
                        kwargs["ExclusiveStartKey"] = last_key
                    r = _table.query(**kwargs)
                    items = r.get("Items", [])
                    items_acc.extend(items)
                    last_key = r.get("LastEvaluatedKey")
                    if not last_key:
                        break
                # delete one by one (can be optimized to batch_write)
                for it in items_acc:
                    pk = it.get("phone_number")
                    sk = it.get("ts")
                    if pk and sk:
                        _table.delete_item(
                            Key={"phone_number": pk, "ts": sk},
                            ConditionExpression="attribute_exists(phone_number) AND attribute_exists(ts)",
                        )
                return _resp(200, {"ok": True, "deleted": len(items_acc)})
            if not phone or not ts:
                return _resp(400, {"ok": False, "error": "phone and ts required (or provide call_sid)"})
            _table.delete_item(
                Key={"phone_number": phone, "ts": ts},
                ConditionExpression="attribute_exists(phone_number) AND attribute_exists(ts)",
            )
            return _resp(200, {"ok": True})

        return _resp(404, {"ok": False, "error": "route not found"})

    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        status = 409 if code in ("ConditionalCheckFailedException",) else 500
        return _resp(status, {"ok": False, "error": str(e)})
    except (BotoCoreError, Exception) as e:
        return _resp(500, {"ok": False, "error": str(e)})



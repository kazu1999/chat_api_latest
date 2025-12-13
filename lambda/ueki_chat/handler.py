import json
import os
from datetime import datetime, timezone
import traceback
from typing import List, Dict, Optional, Any
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError
import urllib.request
import auth  # Local auth helper

CALLS_TABLE_NAME = os.getenv("CALL_LOGS_TABLE_NAME", "ueki-chatbot")
FAQ_TABLE_NAME = os.getenv("FAQ_TABLE_NAME", "ueki-faq")
PROMPTS_TABLE_NAME = os.getenv("PROMPTS_TABLE_NAME", "ueki-prompts")
TASKS_TABLE_NAME = os.getenv("TASKS_TABLE_NAME", "ueki-tasks")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_PROJECT = os.getenv("OPENAI_PROJECT", "")
OPENAI_ORG = os.getenv("OPENAI_ORG", os.getenv("OPENAI_ORGANIZATION", ""))
OPENAI_SECRET_NAME = os.getenv("OPENAI_SECRET_NAME", "")
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"

_ddb = boto3.resource("dynamodb")
_calls = _ddb.Table(CALLS_TABLE_NAME)
_faq = _ddb.Table(FAQ_TABLE_NAME)
_prompts = _ddb.Table(PROMPTS_TABLE_NAME)
_tasks = _ddb.Table(TASKS_TABLE_NAME)

LOG_GROUP_NAME = "/aws/lambda/ueki-chat"


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_default(o: Any):
    if isinstance(o, Decimal):
        try:
            # Prefer int if it is an integer value
            if o % 1 == 0:
                return int(o)
            return float(o)
        except Exception:
            return float(o)
    return str(o)


def _resp(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        },
        "body": json.dumps(body, ensure_ascii=False, default=_json_default),
    }


def _digits_only(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit() or ch == '+')


def _normalize_phone_number(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return raw
    s = _digits_only(str(raw))
    # Normalize Japanese numbers: +81xxxxxxxxx -> 0xxxxxxxxx
    if s.startswith('+81') and len(s) >= 4:
        rest = s[3:]
        # Leading 0 if not already
        if not rest.startswith('0'):
            return '0' + rest
        return rest
    # Fallback: strip leading + if any (store without country code symbol)
    if s.startswith('+'):
        return s[1:]
    return s


def _read_system_prompt(client_id: str) -> str:
    # Try to load from prompts table (id = 'system')
    try:
        r = _prompts.get_item(Key={"client_id": client_id, "id": "system"})
        item = r.get("Item")
        if item and item.get("content"):
            return str(item.get("content"))
    except (BotoCoreError, ClientError):
        pass
    # Fallback default if nothing in DB
    return ""

def _put_system_prompt(client_id: str, markdown: str) -> None:
    _prompts.put_item(Item={
        "client_id": client_id,
        "id": "system",
        "content": markdown,
        "updated_at": _now_iso(),
    })

def _read_func_config(client_id: str) -> Dict:
    try:
        r = _prompts.get_item(Key={"client_id": client_id, "id": "functions"})
        item = r.get("Item")
        if item and item.get("content"):
            raw = item.get("content")
            if isinstance(raw, str):
                return json.loads(raw)
            if isinstance(raw, dict):
                return raw
    except Exception:
        pass
    return {"tools": [], "instructions": ""}

def _put_func_config(client_id: str, cfg: Dict) -> None:
    _prompts.put_item(Item={
        "client_id": client_id,
        "id": "functions",
        "content": cfg,
        "updated_at": _now_iso(),
    })

def _read_ext_tools(client_id: str) -> Dict:
    try:
        r = _prompts.get_item(Key={"client_id": client_id, "id": "ext-tools"})
        item = r.get("Item")
        if item and item.get("content"):
            raw = item.get("content")
            if isinstance(raw, str):
                return json.loads(raw)
            if isinstance(raw, dict):
                return raw
    except Exception:
        pass
    return {"ext_tools": []}

def _put_ext_tools(client_id: str, cfg: Dict) -> None:
    _prompts.put_item(Item={
        "client_id": client_id,
        "id": "ext-tools",
        "content": cfg,
        "updated_at": _now_iso(),
    })

# ---- Tools (Function Calling) implementations ----
def _tool_list_tasks(client_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    # Use Query instead of Scan for tenant isolation
    r = _tasks.query(
        KeyConditionExpression=Key("client_id").eq(client_id),
        Limit=200
    )
    return {"items": r.get("Items", [])}

def _tool_create_task(client_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    name = args.get("name")
    request = args.get("request") or args.get("requirement") or ""
    start_datetime = args.get("start_datetime") or args.get("start_date") or ""
    phone_number = args.get("phone_number") or args.get("phone") or ""
    address = args.get("address") or ""
    if not name:
        return {"error": "name is required"}
    item = {
        "client_id": client_id,
        "name": str(name),
        "request": str(request),
        "start_datetime": str(start_datetime),
        "phone_number": str(phone_number),
        "address": str(address),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    _tasks.put_item(Item=item)
    # Return item without internal keys if possible, but for simplicity returning all
    return {"item": item}

def _tool_get_task(client_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    name = args.get("name")
    if not name:
        return {"error": "name is required"}
    r = _tasks.get_item(Key={"client_id": client_id, "name": str(name)})
    it = r.get("Item")
    if not it:
        return {"error": "not found"}
    return {"item": it}

def _tool_update_task(client_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    name = args.get("name")
    if not name:
        return {"error": "name is required"}
    expr = []
    values: Dict[str, Any] = {":u": _now_iso()}
    names: Dict[str, str] = {"#updated_at": "updated_at"}
    updates = {
        "request": args.get("request"),
        "start_datetime": args.get("start_datetime"),
        "phone_number": args.get("phone_number"),
        "address": args.get("address"),
        "request@legacy": args.get("requirement"),
        "start_datetime@legacy": args.get("start_date"),
    }
    for k, v in updates.items():
        if v is None:
            continue
        db_key = k.split("@", 1)[0]
        expr.append(f"#{db_key} = :{db_key}")
        values[f":{db_key}"] = str(v)
        names[f"#{db_key}"] = db_key
    if not expr:
        return {"error": "nothing to update"}
    r = _tasks.update_item(
        Key={"client_id": client_id, "name": str(name)},
        UpdateExpression="SET " + ", ".join(expr) + ", #updated_at = :u",
        ExpressionAttributeValues=values,
        ExpressionAttributeNames=names,
        ReturnValues="ALL_NEW",
    )
    return {"item": r.get("Attributes")}

def _tool_delete_task(client_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    name = args.get("name")
    if not name:
        return {"error": "name is required"}
    _tasks.delete_item(Key={"client_id": client_id, "name": str(name)})
    return {"ok": True}

_TOOLS_IMPL = {
    "list_tasks": _tool_list_tasks,
    "create_task": _tool_create_task,
    "get_task": _tool_get_task,
    "update_task": _tool_update_task,
    "delete_task": _tool_delete_task,
}


def _fetch_faq_kb_text(client_id: str) -> str:
    items: List[Dict] = []
    last_key = None
    # Use Query instead of Scan for tenant isolation
    for _ in range(5): # Limit pages
        kwargs = {
            "KeyConditionExpression": Key("client_id").eq(client_id),
            "Limit": 200
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        r = _faq.query(**kwargs)
        items.extend(r.get("Items", []))
        last_key = r.get("LastEvaluatedKey")
        if not last_key:
            break
    data = [
        {"question": it.get("question"), "answer": it.get("answer")}
        for it in items
        if it.get("question") and it.get("answer")
    ]
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return ""


def _fetch_history_messages(client_id: str, phone_number: str, limit: int = 20, call_sid: Optional[str] = None) -> List[Dict]:
    try:
        # DB Schema: PK=client_id, SK=ts#phone_number (Wait, we decided sk=ts#phone_number but 
        # actually queries are easier if we have GSI or if SK implies sortable TS.
        # Let's check terraform definition again. 
        # Terraform: PK=client_id, SK=sk (composite).
        # We need to query by phone number. 
        # Option A: Query PK=client_id, filter by phone. (Expensive if many logs)
        # Option B: We need a GSI for phone based lookup? 
        # Actually, for "chat history", we need logs for *this* phone.
        # Let's assume we store SK as `phone_number#ts`. 
        # Then we can query PK=client_id & SK begins_with(phone_number).
        
        # NOTE: Changing storage format to SK=`phone_number#ts`
        prefix = phone_number + "#"
        
        kwargs = {
            "KeyConditionExpression": Key("client_id").eq(client_id) & Key("sk").begins_with(prefix),
            "ScanIndexForward": True, # Oldest first
            "Limit": 50 # fetch a bit more then filter
        }
        
        # If call_sid is provided, we can use GSI CallSidIndex (PK=call_sid) 
        # This is global unique, so we don't strictly need client_id, but good to verify.
        if call_sid:
             kwargs = {
                "IndexName": "CallSidIndex",
                "KeyConditionExpression": Key("call_sid").eq(call_sid),
                "Limit": 200
             }
        
        r = _calls.query(**kwargs)
        items = r.get("Items", [])
        
        # Filter for safety (in case of call_sid collision across tenants? unlikely but good practice)
        if call_sid:
            items = [it for it in items if it.get("client_id") == client_id]
        
        # If not call_sid, we used prefix, so items are already for this phone.
        # But we need to sort by TS. SK is phone#ts, so it is sorted by phone then ts.
        # Since phone is constant, it is sorted by TS.
        
        history: List[Dict] = []
        for it in items:
            if it.get("user_text"):
                history.append({"role": "user", "content": it.get("user_text")})
            if it.get("assistant_text"):
                history.append({"role": "assistant", "content": it.get("assistant_text")})
        
        # Take last N
        if len(history) > limit:
            history = history[-limit:]
            
        return history
    except (BotoCoreError, ClientError):
        return []


def _log_turn(client_id: str, phone_number: str, user_text: str, assistant_text: str, call_sid: Optional[str] = None) -> None:
    try:
        normalized = _normalize_phone_number(phone_number) or phone_number
        ts = _now_iso()
        # SK format: phone_number#ts
        sk = f"{normalized}#{ts}"
        
        item = {
            "client_id": client_id,
            "sk": sk,
            "ts": ts, # Separate attribute for GSI
            "phone_number": normalized, # Attribute for ref
            "user_text": user_text or "",
            "assistant_text": assistant_text or "",
        }
        if call_sid:
            item["call_sid"] = call_sid
            
        _calls.put_item(Item=item)
    except (BotoCoreError, ClientError):
        pass


def _resolve_openai_credentials() -> Dict[str, Optional[str]]:
    # Prefer environment variables
    api_key = OPENAI_API_KEY or None
    project = OPENAI_PROJECT or None
    organization = OPENAI_ORG or None

    if api_key and (project or organization):
        return {"api_key": api_key, "project": project, "organization": organization}

    if OPENAI_SECRET_NAME:
        try:
            sm = boto3.client("secretsmanager")
            r = sm.get_secret_value(SecretId=OPENAI_SECRET_NAME)
            secret = r.get("SecretString") or ""
            if secret.startswith("{"):
                try:
                    js = json.loads(secret)
                    # common keys (broadened)
                    api_key = api_key or next((
                        str(js[k]) for k in (
                            "OPENAI_API_KEY", "api_key", "key", "OPENAI_APIKEY", "openai_api_key", "OPENAI-API-KEY", "API_KEY", "token"
                        ) if js.get(k)
                    ), None)
                    project = project or next((
                        str(js[k]) for k in (
                            "OPENAI_PROJECT", "project", "project_id"
                        ) if js.get(k)
                    ), None)
                    organization = organization or next((
                        str(js[k]) for k in (
                            "OPENAI_ORG", "OPENAI_ORGANIZATION", "organization", "org"
                        ) if js.get(k)
                    ), None)
                    # final fallback: pick first plausible string value (e.g., startswith sk-)
                    if not api_key and isinstance(js, dict):
                        for v in js.values():
                            if isinstance(v, str) and (v.startswith("sk-") or len(v) > 20):
                                api_key = v
                                break
                except Exception:
                    pass
            else:
                # plain string secret -> assume it's the key
                api_key = api_key or (secret if secret else None)
        except Exception:
            print("[ueki-chat] Failed to fetch secret from Secrets Manager", flush=True)

    return {"api_key": api_key, "project": project, "organization": organization}

def _call_openai_raw(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    creds = _resolve_openai_credentials()
    key = creds.get("api_key")
    project = creds.get("project")
    organization = creds.get("organization")
    if not key:
        print("[ueki-chat] OPENAI_API_KEY is not available (env or secret)", flush=True)
        return None
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(OPENAI_CHAT_COMPLETIONS_URL, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    if project:
        req.add_header("OpenAI-Project", project)
    if organization:
        req.add_header("OpenAI-Organization", organization)
    req.add_header("Content-Type", "application/json")
    try:
        print(
            "[ueki-chat] calling OpenAI chat.completions, messages=",
            len(payload.get("messages", [])),
            "tools=",
            bool(payload.get("tools")),
            "project=",
            bool(project),
            "org=",
            bool(organization),
            flush=True,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            js = json.loads(raw)
            return js
    except Exception as e:
        body = None
        try:
            body = getattr(e, 'read', None) and e.read()
            if not body and hasattr(e, 'fp') and getattr(e.fp, 'read', None):
                body = e.fp.read()
        except Exception:
            body = None
        print("[ueki-chat] OpenAI call failed:", repr(e), "body=", (body.decode('utf-8', 'ignore') if body else None), flush=True)
        print(traceback.format_exc(), flush=True)
        return None

def _call_openai(messages: List[Dict]) -> Optional[str]:
    js = _call_openai_raw({
        "model": "gpt-4o-mini",
        "messages": messages,
        "max_tokens": 1200,
        "temperature": 0.3,
    })
    if not js:
        return None
    choices = js.get("choices") or []
    if not choices:
        return None
    return choices[0].get("message", {}).get("content")

def _chat_with_tools(client_id: str, messages: List[Dict]) -> Optional[str]:
    # We need to pass client_id to tools so they can access the correct DB records
    # But tools are called by name from OpenAI.
    # Strategy: Wrap the implementations or inject client_id inside the loop.
    
    tools = _compile_tools_for_openai(client_id)
    if not isinstance(tools, list) or len(tools) == 0:
        return _call_openai(messages)

    max_steps = 4
    current_messages = list(messages)
    for _ in range(max_steps):
        js = _call_openai_raw({
            "model": "gpt-4o-mini",
            "messages": current_messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.3,
            "max_tokens": 1200,
        })
        if not js:
            return None
        choices = js.get("choices") or []
        if not choices:
            return None
        msg = choices[0].get("message", {})
        tool_calls = msg.get("tool_calls") or []
        content = msg.get("content")
        if tool_calls:
            current_messages.append({
                "role": "assistant",
                "tool_calls": tool_calls,
                "content": content or "",
            })
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name")
                args_json = tc.get("function", {}).get("arguments") or "{}"
                try:
                    args = json.loads(args_json)
                except Exception:
                    args = {}
                
                # Execute tool with client_id
                impl = _TOOLS_IMPL.get(fn_name or "")
                result: Any
                if impl:
                    try:
                        # Inject client_id as first arg
                        result = impl(client_id, args)
                    except Exception as e:
                        result = {"error": str(e)}
                else:
                    result = _execute_ext_tool(client_id, fn_name or "", args)
                    
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": fn_name or "",
                    "content": json.dumps(result, ensure_ascii=False),
                })
            continue
        if content:
            return content
        return None
    return None


def _compile_tools_for_openai(client_id: str) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    func_cfg = _read_func_config(client_id)
    if isinstance(func_cfg.get("tools"), list):
        tools.extend(func_cfg.get("tools"))
    ext_cfg = _read_ext_tools(client_id)
    for t in ext_cfg.get("ext_tools", []) or []:
        try:
            name = t.get("name")
            description = t.get("description") or ""
            parameters = t.get("parameters") or {"type": "object", "properties": {}, "additionalProperties": True}
            if name:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": parameters,
                    },
                })
        except Exception:
            continue
    return tools

def _template_str(s: str, args: Dict[str, Any]) -> str:
    out = s
    for k, v in (args or {}).items():
        out = out.replace("{{" + str(k) + "}}", str(v))
    return out

def _execute_ext_tool(client_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _read_ext_tools(client_id)
    by_name: Dict[str, Any] = {}
    for t in cfg.get("ext_tools", []) or []:
        if t.get("name"):
            by_name[t["name"]] = t
    t = by_name.get(tool_name)
    if not t:
        return {"ok": False, "error": f"ext tool not found: {tool_name}"}
    method = (t.get("method") or "GET").upper()
    url = t.get("url") or ""
    headers = t.get("headers") or {}
    body_tpl = t.get("body")
    timeout_sec = int(t.get("timeout") or 10)

    if not url:
        return {"ok": False, "error": "url required"}

    try:
        url_f = _template_str(url, tool_args or {})
        hdrs_f = {k: _template_str(str(v), tool_args or {}) for k, v in headers.items()} if isinstance(headers, dict) else {}
        data_bytes = None
        if body_tpl and method not in ("GET",):
            body_f = _template_str(str(body_tpl), tool_args or {})
            try:
                # if looks like JSON, send as json
                json.loads(body_f)
                data_bytes = body_f.encode("utf-8")
                if not any(k.lower() == "content-type" for k in hdrs_f.keys()):
                    hdrs_f["Content-Type"] = "application/json"
            except Exception:
                data_bytes = body_f.encode("utf-8")

        req = urllib.request.Request(url_f, data=data_bytes, method=method)
        for hk, hv in hdrs_f.items():
            req.add_header(hk, hv)
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", "ignore")
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = text
            return {"ok": True, "status": resp.status, "body": parsed}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handler(event, context):
    try:
        # Auth: Get Client ID
        client_id = auth.get_client_id(event)
        
        http = event.get("requestContext", {}).get("http", {})
        method = http.get("method", "GET").upper()
        path = http.get("path", "/")

        if method == "OPTIONS":
            return _resp(200, {"ok": True})

        if method == "POST" and path == "/chat":
            body = json.loads(event.get("body") or "{}")
            phone_number = _normalize_phone_number(body.get("phone_number"))
            user_text = body.get("user_text")
            call_sid = body.get("call_sid") or body.get("callSid")
            if not phone_number or not user_text:
                return _resp(400, {"ok": False, "error": "phone_number and user_text required"})

            system_prompt = _read_system_prompt(client_id)
            faq_kb = _fetch_faq_kb_text(client_id)
            history = _fetch_history_messages(client_id, phone_number, 20, call_sid)

            messages: List[Dict] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if faq_kb:
                messages.append({"role": "system", "content": f"FAQ_KB\n{faq_kb}"})
            messages.extend(history)
            messages.append({"role": "user", "content": user_text})

            print(f"[ueki-chat] client={client_id} phone={phone_number} history={len(history)}", flush=True)
            
            # Pass client_id to tool execution logic
            reply = _chat_with_tools(client_id, messages) or "申し訳ありません。現在お手続きできません。少し時間をおいてお試しください。"

            _log_turn(client_id, phone_number, user_text, reply, call_sid)
            return _resp(200, {"ok": True, "reply": reply})

        # Prompt management endpoints
        if method == "GET" and path == "/prompt":
            content = _read_system_prompt(client_id)
            return _resp(200, {"ok": True, "id": "system", "content": content})

        if method == "PUT" and path == "/prompt":
            body = json.loads(event.get("body") or "{}")
            content = body.get("content")
            if not isinstance(content, str) or not content.strip():
                return _resp(400, {"ok": False, "error": "content (markdown) required"})
            _put_system_prompt(client_id, content)
            return _resp(200, {"ok": True})

        # Function calling config endpoints
        if method == "GET" and path == "/func-config":
            cfg = _read_func_config(client_id)
            return _resp(200, {"ok": True, "config": cfg})

        if method == "PUT" and path == "/func-config":
            body = json.loads(event.get("body") or "{}")
            cfg = body.get("config")
            if not isinstance(cfg, dict):
                return _resp(400, {"ok": False, "error": "config (object) required"})
            _put_func_config(client_id, cfg)
            return _resp(200, {"ok": True})

        # External tools management endpoints
        if method == "GET" and path == "/ext-tools":
            cfg = _read_ext_tools(client_id)
            return _resp(200, {"ok": True, "config": cfg})

        if method == "PUT" and path == "/ext-tools":
            body = json.loads(event.get("body") or "{}")
            cfg = body.get("config")
            if not isinstance(cfg, dict):
                return _resp(400, {"ok": False, "error": "config (object) required"})
            _put_ext_tools(client_id, cfg)
            return _resp(200, {"ok": True})

        # Chat logs (CloudWatch) endpoint
        if method == "GET" and path == "/chat-logs":
            q = event.get("queryStringParameters") or {}
            try:
                limit = int(q.get("limit") or 100)
            except Exception:
                limit = 100
            try:
                minutes = int(q.get("minutes") or 60)
            except Exception:
                minutes = 60
            limit = max(1, min(200, limit))
            minutes = max(1, min(24 * 60, minutes))

            start_time_ms = None
            try:
                start_time_ms = int(q.get("startTimeMs") or 0)
            except Exception:
                start_time_ms = 0
            if start_time_ms <= 0:
                # lookback window
                now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                start_time_ms = now_ms - minutes * 60 * 1000

            try:
                logs = boto3.client("logs")
                resp = logs.filter_log_events(
                    logGroupName=LOG_GROUP_NAME,
                    startTime=start_time_ms,
                    limit=limit,
                )
                events = resp.get("events", [])
                items = [
                    {
                        "timestamp": e.get("timestamp"),
                        "ingestionTime": e.get("ingestionTime"),
                        "message": e.get("message"),
                        "logStreamName": e.get("logStreamName"),
                        "eventId": e.get("eventId"),
                    }
                    for e in events
                ]
                return _resp(200, {"ok": True, "items": items})
            except Exception as e:
                return _resp(500, {"ok": False, "error": str(e)})

        return _resp(404, {"ok": False, "error": "route not found"})

    except (BotoCoreError, ClientError) as e:
        return _resp(500, {"ok": False, "error": str(e)})
    except Exception as e:
        return _resp(500, {"ok": False, "error": str(e)})

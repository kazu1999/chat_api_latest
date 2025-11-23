import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib import request, parse, error


API_ENDPOINT = os.getenv(
    "API_ENDPOINT",
    "https://so0hxmjon8.execute-api.ap-northeast-1.amazonaws.com",
).rstrip("/")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def http_call(method: str, path: str, body: Optional[Dict[str, Any]] = None, qs: Optional[Dict[str, Any]] = None, timeout: int = 15) -> Tuple[int, Dict[str, Any]]:
    url = f"{API_ENDPOINT}{path}"
    if qs:
        qstr = parse.urlencode(qs)
        url = f"{url}?{qstr}"

    data = None
    headers = {"content-type": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    req = request.Request(url=url, data=data, headers=headers, method=method.upper())
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.getcode()
            try:
                js = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                js = {"_raw": raw.decode("utf-8", errors="replace")}
            return status, js
    except error.HTTPError as e:
        raw = e.read()
        try:
            js = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            js = {"_raw": raw.decode("utf-8", errors="replace")}
        return e.code, js
    except Exception as e:
        return 0, {"error": str(e)}


def assert_ok(label: str, status: int, js: Dict[str, Any]) -> None:
    if status != 200 or not js.get("ok", True):
        print(f"[FAIL] {label}: status={status} body={json.dumps(js, ensure_ascii=False)}")
        sys.exit(1)
    print(f"[OK] {label}")


def test_prompt_api() -> None:
    print("\n=== Prompt API (/prompt) ===")
    # get current
    s, js = http_call("GET", "/prompt")
    assert_ok("Prompt get (before)", s, js)
    old_content = js.get("content", "")

    # update with markdown
    ts = int(time.time())
    new_md = f"# System Prompt (test:{ts})\n\n- これはMarkdownのサンプルです\n- 音調: 丁寧\n"
    s, js = http_call("PUT", "/prompt", {"content": new_md})
    assert_ok("Prompt put", s, js)

    # verify
    s, js = http_call("GET", "/prompt")
    assert_ok("Prompt get (after)", s, js)
    got = js.get("content", "")
    if not isinstance(got, str) or f"test:{ts}" not in got:
        print(f"[FAIL] Prompt verify: content mismatch")
        sys.exit(1)
    print("[OK] Prompt verify")

    # restore previous content (best-effort)
    if isinstance(old_content, str) and old_content != "":
        http_call("PUT", "/prompt", {"content": old_content})


def test_faq_crud() -> None:
    print("\n=== FAQ CRUD ===")
    q = f"営業時間は？(test:{int(time.time())})"

    # create
    s, js = http_call("POST", "/faq", {"question": q, "answer": "10:00〜19:00です。"})
    assert_ok("FAQ create", s, js)

    # get
    s, js = http_call("GET", f"/faq/{parse.quote(q)}")
    assert_ok("FAQ get", s, js)

    # list
    s, js = http_call("GET", "/faqs")
    assert_ok("FAQ list", s, js)

    # update
    s, js = http_call("PUT", f"/faq/{parse.quote(q)}", {"answer": "10:00〜19:00、年中無休。"})
    assert_ok("FAQ update", s, js)

    # delete
    s, js = http_call("DELETE", f"/faq/{parse.quote(q)}")
    assert_ok("FAQ delete", s, js)


def test_calllogs_crud() -> None:
    print("\n=== Call Logs CRUD ===")
    phone = "09012345678"
    ts = _now_iso()

    # create (with call_sid)
    s, js = http_call("POST", "/call", {
        "phone_number": phone,
        "ts": ts,
        "user_text": "予約したい",
        "assistant_text": "お名前をお願いします。",
        "call_sid": "TEST-SID-12345",
    })
    assert_ok("Call create", s, js)

    # get one (by PK)
    s, js = http_call("GET", "/call", None, {"phone": phone, "ts": ts})
    assert_ok("Call get (pk)", s, js)

    # get one (by call_sid)
    s, js = http_call("GET", "/call", None, {"call_sid": "TEST-SID-12345"})
    assert_ok("Call get (call_sid)", s, js)

    # list by phone
    s, js = http_call("GET", "/calls", None, {"phone": phone, "limit": 10})
    assert_ok("Call list", s, js)

    # phones
    s, js = http_call("GET", "/phones")
    assert_ok("Phones list", s, js)

    # update (assistant_text + call_sid change)
    s, js = http_call("PUT", "/call", {
        "phone_number": phone,
        "ts": ts,
        "assistant_text": "では日時をお願いします。",
        "call_sid": "TEST-SID-99999",
    })
    assert_ok("Call update", s, js)

    # delete
    s, js = http_call("DELETE", "/call", None, {"phone": phone, "ts": ts})
    assert_ok("Call delete", s, js)


def test_chat() -> None:
    print("\n=== Chat (/chat) ===")
    phone = "09012345678"
    s, js = http_call("POST", "/chat", {"phone_number": phone, "user_text": "予約したいです"}, None, timeout=30)
    if s != 200 or not js.get("ok"):
        print(f"[FAIL] Chat: status={s} body={json.dumps(js, ensure_ascii=False)}")
        sys.exit(1)
    reply = js.get("reply", "")
    print(f"[OK] Chat reply: {reply[:100]}{'...' if len(reply) > 100 else ''}")


if __name__ == "__main__":
    print(f"API_ENDPOINT: {API_ENDPOINT}")
    #test_prompt_api()
    #test_faq_crud()
    test_calllogs_crud()
    #test_chat()
    print("\nAll tests passed.")



import json
import re
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from boto3.dynamodb.conditions import Key
from openai import OpenAI
from faq import list_faqs

# OpenAIクライアントの初期化
client = OpenAI()

# システムプロンプトの読み込み
with open("system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# セッション管理用の辞書（簡易実装）
SESSIONS = {}

def parse_bot_output(text: str):
    """AIの出力から発話テキストとJSONデータを分離する"""
    spoken = text
    m_spoken = re.search(r"<ASSISTANT_SPOKEN_TEXT>\s*([\s\S]*?)\s*<JSON>", text, re.I)
    m_json   = re.search(r"<JSON>\s*([\s\S]*?)\s*$", text, re.I)
    if m_spoken:
        spoken = m_spoken.group(1).strip()
    js = None
    if m_json:
        try:
            js = json.loads(m_json.group(1))
        except Exception:
            pass
    return spoken, js

def chat_with_bot(
    user_text: str,
    session_id: str = "default",
    external_history: Optional[List[Dict]] = None,
    faq_kb_text: Optional[str] = None,
):
    """
    ボットとチャットする関数
    
    Args:
        user_text (str): ユーザーの入力テキスト
        session_id (str): セッションID（デフォルト: "default"）
    
    Returns:
        dict: レスポンス辞書
            - ok (bool): 成功フラグ
            - spoken (str): AIの発話テキスト
            - json (dict): 構造化データ
            - raw (str): AIの生レスポンス
            - error (str): エラーメッセージ（エラー時のみ）
    """
    if not user_text:
        return {"ok": False, "error": "userText required"}

    # セッション履歴の取得（外部履歴が指定されていればそれを優先）
    history = external_history if external_history is not None else SESSIONS.get(session_id, [])
    messages: List[Dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if faq_kb_text:
        messages.append({"role": "system", "content": f"FAQ_KB\n{faq_kb_text}"})
    messages.extend([*history, {"role": "user", "content": user_text}])

    try:
        # OpenAI Responses API の呼び出し
        resp = client.responses.create(
            model="gpt-4o-mini",
            input=messages,
            max_output_tokens=1200,
        )

        text = getattr(resp, "output_text", "") or ""
        spoken, js = parse_bot_output(text)
        
        # セッション履歴の更新（システムプロンプトを除く）
        new_history = [*messages[1:], {"role": "assistant", "content": text}]
        SESSIONS[session_id] = new_history[-20:]  # 最新20件を保持
        
        return {
            "ok": True, 
            "spoken": spoken, 
            "json": js, 
            "raw": text
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

# =============== DynamoDB Logging ===============
_DDB_REGION = os.getenv("AWS_REGION", "ap-northeast-1")
_DDB_TABLE_NAME = os.getenv("DDB_TABLE_NAME", "ueki-chatbot")

_ddb_resource = None
_ddb_table = None

def _get_ddb_table():
    global _ddb_resource, _ddb_table
    if _ddb_table is not None:
        return _ddb_table
    try:
        _ddb_resource = boto3.resource("dynamodb", region_name=_DDB_REGION)
        _ddb_table = _ddb_resource.Table(_DDB_TABLE_NAME)
        return _ddb_table
    except Exception:
        return None

def log_turn_to_dynamodb(phone_number: str, user_text: str, assistant_text: str) -> bool:
    table = _get_ddb_table()
    if table is None:
        return False
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    item = {
        "phone_number": phone_number,
        "ts": ts,
        "user_text": user_text,
        "assistant_text": assistant_text,
    }
    try:
        table.put_item(Item=item)
        return True
    except (BotoCoreError, ClientError):
        return False


def fetch_turns_from_dynamodb(phone_number: str, limit: int = 20) -> List[Dict]:
    """DynamoDBから最新の会話ターンを最大limit件取得（昇順で返す）。"""
    table = _get_ddb_table()
    if table is None:
        return []
    try:
        # 降順でlimit件を取り、昇順に並べ替えて返す
        resp = table.query(
            KeyConditionExpression=Key("phone_number").eq(phone_number),
            ScanIndexForward=False,
            Limit=limit,
        )
        items = resp.get("Items", [])
        items.sort(key=lambda x: x.get("ts", ""))
        return items
    except (BotoCoreError, ClientError):
        return []


def build_history_messages_from_turns(turns: List[Dict]) -> List[Dict]:
    """DynamoDBのターン配列からOpenAIのmessages履歴を構築。"""
    history: List[Dict] = []
    for t in turns:
        user_text = t.get("user_text")
        assistant_text = t.get("assistant_text")
        if user_text:
            history.append({"role": "user", "content": user_text})
        if assistant_text:
            history.append({"role": "assistant", "content": assistant_text})
    return history


# =============== FAQ Lookup (DynamoDB) ===============
def _fetch_all_faqs(max_pages: int = 10, page_limit: int = 200) -> List[Dict]:
    """Retrieve FAQ items by paginating scans via list_faqs()."""
    items: List[Dict] = []
    last_key: Optional[Dict] = None
    pages = 0
    while pages < max_pages:
        res = list_faqs(limit=page_limit, last_evaluated_key=last_key)
        if not res.get("ok"):
            break
        items.extend(res.get("items", []))
        last_key = res.get("last_evaluated_key")
        pages += 1
        if not last_key:
            break
    return items

def build_faq_kb_text() -> str:
    faqs = _fetch_all_faqs()
    if not faqs:
        return ""
    # JSONにして渡すとモデルが参照しやすい
    try:
        return json.dumps([
            {"question": f.get("question"), "answer": f.get("answer")}
            for f in faqs if f.get("question") and f.get("answer")
        ], ensure_ascii=False)
    except Exception:
        # フォールバック: プレーンテキスト
        lines = ["- Q: {q}\n  A: {a}".format(q=f.get("question"), a=f.get("answer")) for f in faqs]
        return "\n".join(lines)


def chat_with_logging(phone_number: str, user_text: str) -> str:
    """
    入力: phone_number, user_text
    出力: assistantの返答（文字列）。DynamoDBへ1ターン分を保存。
    """
    # FAQナレッジを全件プロンプトへ投入
    faq_kb = build_faq_kb_text()

    # DBの過去ログを履歴として使用し、FAQ_KBも渡してLLMに問い合わせ
    turns = fetch_turns_from_dynamodb(phone_number=phone_number, limit=20)
    db_history = build_history_messages_from_turns(turns)
    result = chat_with_bot(
        user_text=user_text,
        session_id=phone_number,
        external_history=db_history,
        faq_kb_text=faq_kb,
    )
    if not result.get("ok"):
        return result.get("error", "Error")

    assistant_spoken: Optional[str] = result.get("spoken") or result.get("raw") or ""
    # ログ保存（失敗しても会話は継続）
    log_turn_to_dynamodb(phone_number=phone_number, user_text=user_text, assistant_text=assistant_spoken)
    return assistant_spoken

def clear_session(session_id: str = "default"):
    """指定されたセッションの履歴をクリアする"""
    if session_id in SESSIONS:
        del SESSIONS[session_id]
        return True
    return False

def get_session_history(session_id: str = "default"):
    """指定されたセッションの履歴を取得する"""
    return SESSIONS.get(session_id, [])

def list_sessions():
    """アクティブなセッション一覧を取得する"""
    return list(SESSIONS.keys())

# テスト用のサンプル実行
if __name__ == "__main__":
    print("=== 電話AIボット テスト (DynamoDBログ付き) ===")
    print("電話番号を入力してください（例: 09012345678）。終了するには 'quit'\n")

    phone = input("電話番号: ").strip()
    if phone.lower() in ["quit", "exit", "終了"] or not phone:
        raise SystemExit(0)

    print("\n初期メッセージを送ります: こんにちは")
    print("AI:", chat_with_logging(phone, "こんにちは"))

    while True:
        user_input = input("\nあなた: ").strip()
        if user_input.lower() in ['quit', 'exit', '終了']:
            print("会話を終了します。")
            break
        reply = chat_with_logging(phone, user_input)
        print("AI:", reply)

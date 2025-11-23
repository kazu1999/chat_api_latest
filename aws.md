## AWS 構成まとめ

- **リージョン**: ap-northeast-1
- **IaC**: Terraform（`terraform/` 配下）

### DynamoDB

1) 会話ログテーブル（コールログ）
- **テーブル名**: `ueki-chatbot`
- **キー設計**:
  - パーティションキー: `phone_number` (S)
  - ソートキー: `ts` (S, ISO8601 UTC, 秒精度)
- **代表的な属性**:
  - `phone_number`: 文字列（電話番号）
  - `ts`: 文字列（ISO8601: 2025-10-01T12:34:56+00:00）
  - `user_text`: 文字列（ユーザー発話）
  - `assistant_text`: 文字列（AI応答）
- **キャパシティ**: オンデマンド（PAY_PER_REQUEST）
- **PITR**: 有効

2) FAQテーブル
- **テーブル名**: `ueki-faq`
- **キー設計**:
  - パーティションキー: `question` (S)
- **代表的な属性**:
  - `question`: 文字列（質問本文、ユニーク）
  - `answer`: 文字列（回答）
  - `created_at`: 文字列（ISO8601）
  - `updated_at`: 文字列（ISO8601）
- **キャパシティ**: オンデマンド（PAY_PER_REQUEST）
- **PITR**: 有効

3) プロンプトテーブル（Markdown格納）
- **テーブル名**: `ueki-prompts`
- **キー設計**:
  - パーティションキー: `id` (S) — 既定は `system`
- **代表的な属性**:
  - `id`: 文字列（例: `system`）
  - `content`: 文字列（Markdown でプロンプト本文、または FunctionConfig）
  - `updated_at`: 文字列（ISO8601）
- **キャパシティ**: オンデマンド（PAY_PER_REQUEST）
- **PITR**: 有効

4) タスクテーブル
- **テーブル名**: `ueki-tasks`
- **キー設計**:
  - パーティションキー: `name` (S)
- **代表的な属性**:
  - `name`, `phone_number`, `address`, `start_datetime`, `request`, `created_at`, `updated_at`
- **キャパシティ**: オンデマンド（PAY_PER_REQUEST）
- **PITR**: 有効

### Lambda（FAQ CRUD API）

- **関数名**: `ueki-faq`
- **ランタイム**: Python 3.11
- **ハンドラ**: `handler.handler`（パス: `lambda/ueki_faq/handler.py`）
- **環境変数**:
  - `FAQ_TABLE_NAME` = `ueki-faq`
- **IAM**:
  - 対象テーブル（`ueki-faq`）への DynamoDB 権限
  - CloudWatch Logs 出力権限

### Lambda（Call Logs CRUD API）

- **関数名**: `ueki-calllogs`
- **ランタイム**: Python 3.11
- **ハンドラ**: `handler.handler`（パス: `lambda/ueki_calllogs/handler.py`）
- **環境変数**:
  - `CALL_LOGS_TABLE_NAME` = `ueki-chatbot`
- **IAM**:
  - 対象テーブル（`ueki-chatbot`）への DynamoDB 権限
  - CloudWatch Logs 出力権限

### Lambda（Chat API）

- **関数名**: `ueki-chat`
- **ランタイム**: Python 3.11
- **ハンドラ**: `handler.handler`（パス: `lambda/ueki_chat/handler.py`）
- **タイムアウト**: 10 秒
- **環境変数**:
  - `CALL_LOGS_TABLE_NAME` = `ueki-chatbot`
  - `FAQ_TABLE_NAME` = `ueki-faq`
  - `PROMPTS_TABLE_NAME` = `ueki-prompts`
  - `TASKS_TABLE_NAME` = `ueki-tasks`
  - `OPENAI_SECRET_NAME` = `UEKI_OPENAI_APIKEY`（Secrets Manager から OpenAI API キー等を解決）
- **IAM**:
  - 対象テーブル（`ueki-chatbot`, `ueki-faq`, `ueki-prompts`, `ueki-tasks`）への DynamoDB 権限
  - CloudWatch Logs 出力権限 + CloudWatch Logs 読み取り権限（`logs:FilterLogEvents` 等, `/chat-logs` 用）
  - Secrets Manager 読み取り権限（`UEKI_OPENAI_APIKEY`）

### Lambda（Tasks CRUD API）

- **関数名**: `ueki-tasks`
- **ランタイム**: Python 3.11
- **ハンドラ**: `handler.handler`（パス: `lambda/ueki_tasks/handler.py`）
- **環境変数**:
  - `TASKS_TABLE_NAME` = `ueki-tasks`
- **IAM**:
  - 対象テーブル（`ueki-tasks`）への DynamoDB 権限
  - CloudWatch Logs 出力権限

### Lambda（Realtime API - OpenAI Realtime API + Twilio）

- **関数名**: `ueki-realtime`
- **ランタイム**: Python 3.11
- **ハンドラ**: `handler.handler`（パス: `lambda/ueki_realtime/handler.py`）
- **タイムアウト**: 900秒（15分、WebSocket接続のため）
- **メモリ**: 512MB
- **環境変数**:
  - `CALL_LOGS_TABLE_NAME` = `ueki-chatbot`
  - `FAQ_TABLE_NAME` = `ueki-faq`
  - `PROMPTS_TABLE_NAME` = `ueki-prompts`
  - `TASKS_TABLE_NAME` = `ueki-tasks`
  - `OPENAI_API_KEY` = OpenAI APIキー
  - `OPENAI_SECRET_NAME` = `UEKI_OPENAI_APIKEY`（Secrets Manager から OpenAI API キー等を解決）
  - `OPENAI_WEBHOOK_SECRET` = OpenAI Realtime APIのWebhook Secret
- **IAM**:
  - 対象テーブル（`ueki-chatbot`, `ueki-faq`, `ueki-prompts`, `ueki-tasks`）への DynamoDB 権限
  - CloudWatch Logs 出力権限
  - Secrets Manager 読み取り権限（`UEKI_OPENAI_APIKEY`）
- **エンドポイント**: Lambda Function URL（Webhook用）
  - OpenAI Realtime APIのWebhook URLとして設定
  - TwilioのWebhook URLとして設定（オプション）

### API Gateway（HTTP API v2）

- **API名**: `ueki`
- **ベースURL**: `https://so0hxmjon8.execute-api.ap-northeast-1.amazonaws.com`
- **ルーティング（Lambda プロキシ統合）**:
  - `GET /faqs` … FAQ一覧（最大200件/リクエスト）
  - `POST /faq` … FAQ作成（body: `{ "question": string, "answer": string }`）
  - `GET /faq/{question}` … FAQ取得（`{question}` は URL エンコード）
  - `PUT /faq/{question}` … FAQ更新（body: `{ "answer": string }`）
  - `DELETE /faq/{question}` … FAQ削除
  - `POST /chat` … 会話実行（入力: `{ phone_number, user_text, call_sid? }` → 出力: `{ ok, reply }`）
  - `GET /func-config` … Function Calling 設定（tools/instructions）取得
  - `PUT /func-config` … Function Calling 設定の更新（body: `{ "config": {...} }`）
  - `GET /prompt` … 現在のシステムプロンプト（Markdown）取得（`{"ok":true,"id":"system","content":"..."}`）
  - `PUT /prompt` … システムプロンプト（Markdown）更新（body: `{ "content": string }`）
  - `GET /calls` … コールログ一覧（必須: `phone`、任意: `from`/`to`/`limit`/`next_token`、任意 `order=asc|desc`）
  - `GET /phones` … 既存電話番号の一覧（重複排除）
  - `POST /call` … コールログ作成（body: `{ "phone_number": string, "ts?": string, "user_text?": string, "assistant_text?": string, "call_sid?": string }`）
  - `GET /call` … 1件取得（query: `phone`, `ts` または `call_sid`）
  - `PUT /call` … 更新（body: `{ "phone_number": string, "ts": string, "user_text?": string, "assistant_text?": string, "call_sid?": string }`）
  - `DELETE /call` … 削除（query: `phone`, `ts` で1件、または `call_sid` でセッション一括削除）
  - `GET /tasks` … タスク一覧
  - `POST /task` … タスク作成（body: `{ name, phone_number?, address?, start_datetime?, request? }`）
  - `GET /task/{name}` … タスク取得
  - `PUT /task/{name}` … タスク更新（任意フィールドのみ）
  - `DELETE /task/{name}` … タスク削除
  - `GET /chat-logs` … CloudWatch Logs（`ueki-chat`）の最近のイベント取得（query: `minutes`/`limit` または `startTimeMs`）
  - `GET /ext-tools` … 外部APIツール定義の取得（`{"ok":true,"config":{"ext_tools":[...]}}`）
  - `PUT /ext-tools` … 外部APIツール定義の更新（body: `{ "config": {"ext_tools": [...] } }`）
- **レスポンス**（共通）:
  - 成功: `{ "ok": true, ... }`
  - 失敗: `{ "ok": false, "error": string }`

### デプロイ/確認コマンド

Terraform（初回/更新）:
```bash
cd terraform
terraform init
terraform apply -auto-approve
```

出力の取得（エンドポイント/テーブル名）:
```bash
terraform output -raw ueki_api_endpoint
terraform output -raw table_name        # ueki-chatbot
terraform output -raw faq_table_name    # ueki-faq
terraform output -raw ueki_realtime_function_url  # Lambda Function URL for webhooks
```

FAQ API 動作確認例:
```bash
EP=$(terraform output -raw ueki_api_endpoint)

# 一覧
curl -s $EP/faqs | jq .

# 作成
curl -s -X POST $EP/faq \
  -H 'content-type: application/json' \
  -d '{"question":"営業時間は？","answer":"10:00〜19:00です。"}' | jq .

# 取得（URLエンコード）
curl -s "$EP/faq/%E5%96%B6%E6%A5%AD%E6%99%82%E9%96%93%E3%81%AF%EF%BC%9F" | jq .

# 更新
curl -s -X PUT "$EP/faq/%E5%96%B6%E6%A5%AD%E6%99%82%E9%96%93%E3%81%AF%EF%BC%9F" \
  -H 'content-type: application/json' \
  -d '{"answer":"10:00〜19:00、年中無休。"}' | jq .

# 削除
curl -s -X DELETE "$EP/faq/%E5%96%B6%E6%A5%AD%E6%99%82%E9%96%93%E3%81%AF%EF%BC%9F" | jq .
```

Chat API 動作確認例:
```bash
EP=$(terraform output -raw ueki_api_endpoint)

curl -s -X POST "$EP/chat" \
  -H 'content-type: application/json' \
  -d '{"phone_number":"09012345678","user_text":"予約したいです","call_sid":"TEST-CALL-1"}' | jq .
```

Function Config API 例:
```bash
EP=$(terraform output -raw ueki_api_endpoint)

# 取得
curl -s "$EP/func-config" | jq .

# 更新（parameters.txt を投入する例）
python3 - <<'PY'
import json,sys
cfg=json.load(open('chat_api/parameters.txt'))
print(json.dumps({"config":cfg}))
PY
```

Call Logs API 動作確認例:
```bash
EP=$(terraform output -raw ueki_api_endpoint)

# 電話番号一覧
curl -s "$EP/phones" | jq .

# コールログ一覧（電話番号必須、期間指定任意）
curl -s "$EP/calls?phone=09012345678&limit=20" | jq .

# 1件作成（ts省略時はLambda側で現在時刻を補完）
curl -s -X POST "$EP/call" \
  -H 'content-type: application/json' \
  -d '{"phone_number":"09012345678","user_text":"予約したい","assistant_text":"お名前をお願いします。"}' | jq .

# 1件作成（call_sid を付与する例）
curl -s -X POST "$EP/call" \
  -H 'content-type: application/json' \
  -d '{"phone_number":"09012345678","user_text":"予約したい","assistant_text":"お名前をお願いします。","call_sid":"TEST-SID-12345"}' | jq .

# 1件取得
curl -s "$EP/call?phone=09012345678&ts=2025-10-01T12:34:56+00:00" | jq .

# call_sid でセッション取得（配列 items）
curl -s "$EP/call?call_sid=TEST-SID-12345" | jq .

# 1件更新（一部フィールドのみ可）
curl -s -X PUT "$EP/call" \
  -H 'content-type: application/json' \
  -d '{"phone_number":"09012345678","ts":"2025-10-01T12:34:56+00:00","assistant_text":"では日時をお願いします。"}' | jq .

# 1件削除
curl -s -X DELETE "$EP/call?phone=09012345678&ts=2025-10-01T12:34:56+00:00" | jq .

# セッション一括削除（call_sid 指定）
curl -s -X DELETE "$EP/call?call_sid=TEST-SID-12345" | jq .
```

Tasks API 動作確認例:
```bash
EP=$(terraform output -raw ueki_api_endpoint)

# 作成
curl -s -X POST "$EP/task" \
  -H 'content-type: application/json' \
  -d '{"name":"山田太郎","phone_number":"09012345678","address":"東京都港区","start_datetime":"2025-11-01 10:00","request":"剪定"}' | jq .

# 一覧
curl -s "$EP/tasks" | jq .

# 取得
curl -s "$EP/task/%E5%B1%B1%E7%94%B0%E5%A4%AA%E9%83%8E" | jq .

# 更新（一部フィールド）
curl -s -X PUT "$EP/task/%E5%B1%B1%E7%94%B0%E5%A4%AA%E9%83%8E" \
  -H 'content-type: application/json' \
  -d '{"request":"剪定と清掃"}' | jq .

# 削除
curl -s -X DELETE "$EP/task/%E5%B1%B1%E7%94%B0%E5%A4%AA%E9%83%8E" | jq .
```

Chat Logs（CloudWatch）API 例:
```bash
EP=$(terraform output -raw ueki_api_endpoint)

# 直近60分から最大100件
curl -s "$EP/chat-logs?minutes=60&limit=100" | jq .

# 任意の開始時刻（エポックms）から検索
START=$(($(date +%s)*1000-10*60*1000))
curl -s "$EP/chat-logs?startTimeMs=$START&minutes=20&limit=200" | jq .
```

External APIs（ext-tools）例:
```bash
EP=$(terraform output -raw ueki_api_endpoint)

# 取得
curl -s "$EP/ext-tools" | jq .

# 更新（Open‑Meteoの東京今日/汎用などを登録）
cat > /tmp/ext.json <<'JSON'
{
  "config": {
    "ext_tools": [
      {
        "name": "open_meteo_daily_tokyo_today",
        "description": "Get today's daily summary for Tokyo (Asia/Tokyo)",
        "method": "GET",
        "url": "https://api.open-meteo.com/v1/forecast?latitude=35.68&longitude=139.77&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code&timezone=Asia%2FTokyo&forecast_days=1",
        "parameters": {"type":"object","properties":{},"additionalProperties":false},
        "timeout": 10
      }
    ]
  }
}
JSON
curl -s -X PUT "$EP/ext-tools" -H 'content-type: application/json' --data-binary @/tmp/ext.json | jq .
```

Realtime API（Lambda Function URL）設定例:
```bash
# Lambda Function URLを取得
REALTIME_URL=$(terraform output -raw ueki_realtime_function_url)
echo "Realtime Function URL: $REALTIME_URL"

# OpenAI Realtime APIのWebhook URLとして設定
# OpenAI ConsoleでこのURLを設定してください

# TwilioのWebhook URLとして設定（オプション）
# Twilio ConsoleでこのURLを設定してください
```

### 備考

- 今後 FAQ 件数が増加する場合は、API レイヤーでのページング・全文検索・要約投入などの最適化をご検討ください。
- セキュリティ（任意）: 認証/認可、CORS、WAF、APIキー 等の導入を推奨します。
  - `/chat-logs` は CloudWatch Logs 読み取り権限が必要です。
  - OpenAI キーは Secrets Manager（`UEKI_OPENAI_APIKEY`）での管理を推奨します。
  - 電話番号の正規化: 受信した電話番号は保存前に正規化します（`+81xxxxxxxxxx` → `0xxxxxxxxxx`、その他の `+` 除去）。これにより `080...` と `+8180...` が同一キーに統合されます。
  - チャット履歴の参照: `call_sid` が指定されたチャットでは、同一電話番号かつ同一 `call_sid` のログのみを履歴として参照し、他セッションの混在を防ぎます。



# Chat API – test.py の使い方

このリポジトリは、電話受付AI（予約＋FAQ）をローカルから関数/CLIで試せるサンプルを含みます。ここでは `test.py` の使い方を説明します。

## 前提
- Python 3.11+（3.12/3.13でも可）
- OpenAI API キー（環境変数 `OPENAI_API_KEY` に設定）
- AWS 認証（CLI などで設定済み）
- DynamoDB テーブルが存在
  - 会話ログ: `ueki-chatbot`（PK: `phone_number`, SK: `ts`）
  - FAQ: `ueki-faq`（PK: `question`）
  - どちらも Terraform で作成済み（`terraform/`）

## セットアップ
```bash
pip install -r requirements.txt
```

必要に応じて環境変数を設定します（既定値があるため通常は不要）。
```bash
export OPENAI_API_KEY="sk-..."
export AWS_REGION="ap-northeast-1"         # 既定: ap-northeast-1
export DDB_TABLE_NAME="ueki-chatbot"       # 既定: ueki-chatbot
export FAQ_TABLE_NAME="ueki-faq"           # 既定: ueki-faq
```

## 使い方（対話モード）
`test.py` を直接実行すると、電話番号を聞かれ、その番号を会話セッションIDとして対話できます。各ターンは DynamoDB に保存されます。
```bash
python test.py
# プロンプトに従って電話番号を入力 → メッセージを送る
```

動作概要:
- 会話履歴は DynamoDB `ueki-chatbot` から取得し、続きの文脈として利用
- FAQ は DynamoDB `ueki-faq` を全件取得し、`FAQ_KB` としてプロンプトに投入
- 返答は `Responses API (gpt-4o-mini)` を利用

## 使い方（コードから呼び出し）
```python
from test import chat_with_logging, chat_with_bot

# 1) ログ記録込みで1ターン実行（返り値は応答テキスト）
reply = chat_with_logging("09012345678", "予約したいです")
print(reply)

# 2) 低レベルAPI（構造化レスポンスが欲しい場合）
res = chat_with_bot(user_text="こんにちは", session_id="09012345678")
# res = { ok, spoken, json, raw }
print(res["spoken"])  # 発話テキスト
```

## DynamoDB スキーマ（参考）
- 会話ログ `ueki-chatbot`
  - PK: `phone_number` (S)
  - SK: `ts` (S, ISO8601 UTC, 例: `2025-10-01T12:34:56+00:00`)
  - 属性: `user_text` (S), `assistant_text` (S), `call_sid` (S?)

- FAQ `ueki-faq`
  - PK: `question` (S)
  - 属性: `answer` (S), `created_at` (S, ISO8601), `updated_at` (S, ISO8601)

- プロンプト/設定 `ueki-prompts`
  - PK: `id` (S) — 既定は `system`
  - 属性: `content` (S, Markdown または JSON), `updated_at` (S, ISO8601)
  - 用途:
    - `id=system` の `content` はシステムプロンプト（Markdown）
    - `id=functions` などで Function Config / Ext Tools をJSONで格納（APIで取得/更新）

- タスク `ueki-tasks`
  - PK: `name` (S)
  - 推奨属性: `phone_number` (S), `address` (S), `start_datetime` (S), `request` (S), `created_at` (S), `updated_at` (S)

## よくあるエラーと対処
- OpenAI 認証エラー: `OPENAI_API_KEY` を設定してください
- AWS 認証エラー: `aws sts get-caller-identity` で認証を確認してください
- DynamoDB テーブル未作成: `terraform/` で `terraform apply -auto-approve`
- モデル名エラー: `gpt-4o-mini` を利用できるアカウント/リージョンか確認

## 関連ドキュメント
- AWS構成の詳細: `aws.md`
- FAQ CRUDのローカル関数: `faq.py`

## API 仕様（AWS Lambda + API Gateway）

ベースURL: `https://so0hxmjon8.execute-api.ap-northeast-1.amazonaws.com`

### Chat Lambda エンドポイント一覧
- OPTIONS 任意パス（CORSプリフライト応答）
- POST `/chat`（会話実行）
- GET `/prompt`（システムプロンプト取得）
- PUT `/prompt`（システムプロンプト更新）
- GET `/func-config`（Function Calling 設定取得）
- PUT `/func-config`（Function Calling 設定更新）
- GET `/ext-tools`（外部APIツール定義取得）
- PUT `/ext-tools`（外部APIツール定義更新）
- GET `/chat-logs`（CloudWatch Logsの最近イベント取得）

### Call Logs Lambda エンドポイント一覧
- OPTIONS 任意パス（CORSプリフライト応答）
- GET `/calls`（一覧・ページング・期間検索）
- GET `/phones`（登録済み電話番号一覧）
- POST `/call`（1件作成）
- GET `/call`（1件またはcall_sidでセッション取得）
- PUT `/call`（1件更新）
- DELETE `/call`（1件削除 または call_sid でセッション一括削除）
- GET `/recordings`（Twilio録音一覧: `?call_sid=CA...`）
- GET `/recording/{sid}`（Twilio録音ストリーム: `?format=mp3|wav`、拡張子指定も可）
- GET `/transcription`（Whisperによる文字起こし: `?recording_sid=RE...&format=mp3|wav`）

### FAQ Lambda エンドポイント一覧
- OPTIONS 任意パス（CORSプリフライト応答）
- GET `/faqs`（一覧）
- POST `/faq`（作成）
- GET `/faq/{question}`（取得）
- PUT `/faq/{question}`（更新）
- DELETE `/faq/{question}`（削除）

### Tasks Lambda エンドポイント一覧
- OPTIONS 任意パス（CORSプリフライト応答）
- GET `/tasks`（一覧）
- POST `/task`（作成）
- GET `/task/{name}`（取得）
- PUT `/task/{name}`（更新）
- DELETE `/task/{name}`（削除）

– POST `/chat`（会話実行）
  - 入力（JSON）:
    - `phone_number`: string（セッションIDとして使用）
    - `user_text`: string（ユーザーの発話）
    - `call_sid`?: string（同一通話セッションを束ねたい場合）
  - 出力（JSON）:
    - `ok`: boolean
    - `reply`: string（AIの返答テキスト）
    - `error`?: string（失敗時）
  - 例:
    ```bash
    curl -s -X POST "https://so0hxmjon8.execute-api.ap-northeast-1.amazonaws.com/chat" \
      -H 'content-type: application/json' \
      -d '{"phone_number":"09012345678","user_text":"予約したいです"}' | jq .
    ```

- FAQ CRUD（参考）
  - `GET /faqs`（一覧）
  - `POST /faq`（作成） body: `{ "question": string, "answer": string }`
  - `GET /faq/{question}`（取得）
  - `PUT /faq/{question}`（更新） body: `{ "answer": string }`
  - `DELETE /faq/{question}`（削除）

- Call Logs CRUD（参考）
  - `GET /phones`（登録済み電話番号一覧）
  - `GET /calls?phone=...&from=...&to=...&limit=...&next_token=...&order=asc|desc`（一覧）
    - 注意: `limit` は「アイテム（ターン）」件数の上限です（セッション数ではありません）
    - 最新から取得したい場合は `order=desc` を推奨
  - `POST /call`（作成） body: `{ "phone_number": string, "ts?": string, "user_text?": string, "assistant_text?": string, "call_sid?": string }`
  - `GET /call?phone=...&ts=...` または `GET /call?call_sid=...`（取得）
  - `PUT /call`（更新） body: `{ "phone_number": string, "ts": string, "user_text?": string, "assistant_text?": string }`
  - `DELETE /call?phone=...&ts=...`（1件削除）

-- Recordings（Twilio 連携）
  - 事前準備（Terraform 変数 または Lambda 環境変数）
    - `TWILIO_ACCOUNT_SID`
    - `TWILIO_AUTH_TOKEN`
    - Terraform では `variables.tf` の `twilio_account_sid` / `twilio_auth_token` を設定 → `terraform apply`
  - 一覧
    - `GET /recordings?call_sid=CAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
    - 応答例: `{"ok":true,"items":[{"sid":"RE...","duration":"45","date_created":"...","media_format":"mp3"}, ...]}`
  - 音声ストリーム
    - `GET /recording/RExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?format=mp3`
    - レスポンス: `content-type: audio/mpeg`（Base64でLambda→APIGWを通過済み）
    - ブラウザ `<audio>` ソースやダウンロードに利用可能

-- Transcription（Whisper 連携）
  - 秘密情報の取得
    - OpenAI APIキーは Secrets Manager から取得（シークレット名: `UEKI_OPENAI_APIKEY`）
    - 形式はプレーン（`sk-...`）または JSON のどちらでも可:
      - 例（JSON）: `{"OPENAI_API_KEY":"sk-...","OPENAI_PROJECT":"proj_..."}`（`OPENAI_PROJECT` は任意）
  - エンドポイント
    - `GET /transcription?recording_sid=RE...&format=mp3`
    - その録音1件のみを文字起こしして返却
    - 成功例: `{"ok":true,"text":"...", "segments":[...]}`
    - 失敗時: `{"ok":false,"error":"..."}`
  - タイムアウト
    - Lambda タイムアウト: 30秒
    - 内訳の目安: Twilio 取得 ~10秒 / Whisper 呼び出し ~25秒（合計30秒以内に収まるよう調整）
    - 30秒を超える処理は同期APIの都合で失敗する可能性あり

-- Tasks CRUD
  - `GET /tasks`（一覧）
  - `POST /task`（作成） body: `{ "name": string, "phone_number?": string, "address?": string, "start_datetime?": string, "request?": string }`
  - `GET /task/{name}`（取得）
  - `PUT /task/{name}`（更新） body: 任意フィールドのみ
  - `DELETE /task/{name}`（削除）

-- System Prompt / Function Config / Ext Tools
  - `GET /prompt`（現在のシステムプロンプト取得; `{"ok":true,"id":"system","content":"..."}`）
  - `PUT /prompt`（システムプロンプト更新） body: `{ "content": string }`
  - `GET /func-config`（Function Calling 設定取得; tools/instructionsを含むJSON）
  - `PUT /func-config`（Function Calling 設定更新） body: `{ "config": {...} }`
  - `GET /ext-tools`（外部APIツール定義の取得） → `{"ok":true,"config":{"ext_tools":[...]}}`
  - `PUT /ext-tools`（外部APIツール定義の更新） body: `{ "config": {"ext_tools": [...] } }`

-- Chat Logs（CloudWatch 参照）
  - `GET /chat-logs?minutes=60&limit=100` または `GET /chat-logs?startTimeMs=...&minutes=...&limit=...`
  - 直近の`ueki-chat` Lambda ログイベントを取得（運用確認用）
  - 例:
    ```bash
    EP=https://so0hxmjon8.execute-api.ap-northeast-1.amazonaws.com
    curl -s "$EP/chat-logs?minutes=30&limit=50" | jq .
    ```

認証/認可は現状なし。CORSは全許可です。

## テストスクリプト

API一式の疎通確認用スクリプト `chat_api_test.py` を同梱しています。

実行:
```bash
python chat_api_test.py
# 必要に応じてベースURLを上書き
# API_ENDPOINT="https://..." python chat_api_test.py
```

実施内容:
- FAQ CRUD（作成/取得/一覧/更新/削除）
- Call Logs CRUD（作成/取得/一覧/電話番号一覧/更新/削除）
- Chat API（/chat）応答確認

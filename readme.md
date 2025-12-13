# Chat API – マルチテナント対応AI電話バックエンド

このリポジトリは、AIリアルタイム電話システム（Chat API）のバックエンド実装です。
AWS Lambda + API Gateway (HTTP API) + DynamoDB + Cognito を使用し、マルチテナント構成で以下の機能を提供します。

- **Chat**: 会話履歴の管理、AI応答の生成（OpenAI API連携）
- **Call Logs**: 通話ログの保存・検索、録音データの管理（Twilio連携）、文字起こし（Whisper連携）
- **FAQ**: FAQナレッジベースのCRUD管理
- **Tasks**: AIが作成するタスクデータの管理
- **Settings**: プロンプト、Function Calling設定、外部ツール設定の管理

## 前提条件

- **AWS アカウント**: Lambda, API Gateway, DynamoDB, Cognito, Secrets Manager を使用
- **OpenAI API Key**: AI応答およびWhisperに使用
- **Twilio Account**: 通話録音の取得に使用（オプション）
- **Python 3.11+**

## 気をつけること
terraform/variables.tf twilio_account_sidとauth_tokenを埋めてからapplyすること
variable "twilio_account_sid" {
  type        = string
  description = "Twilio Account SID"
  sensitive   = true
  default     = "TO BE FILLED"
}

variable "twilio_auth_token" {
  type        = string
  description = "Twilio Auth Token"
  sensitive   = true
  default     = "TO BE FILLED"
}

## アーキテクチャ概要

### マルチテナント設計
- **認証**: AWS Cognito User Pool を使用。各ユーザーは `custom:tenant_id` 属性を持ちます。
- **データ分離**: すべての DynamoDB テーブルは `client_id` (Tenant ID) をパーティションキーとして持ち、テナントごとのデータを論理的に分離しています。
- **API**: API Gateway の Cognito Authorizer により、リクエストには有効な JWT が必要です。Lambda は JWT または `x-client-id` ヘッダーから `client_id` を特定し、適切なデータにアクセスします。

### DynamoDB テーブル構成
Terraform で作成されます (`terraform/`)。

1.  **Conversation Logs (`app-logs`)**
    - PK: `client_id` (S)
    - SK: `sk` (S) - 形式: `{phone_number}#{timestamp}`
    - GSI: `TsIndex` (PK: `client_id`, SK: `ts`), `CallSidIndex` (PK: `call_sid`)
2.  **FAQ (`app-faq`)**
    - PK: `client_id` (S)
    - SK: `question` (S)
3.  **Prompts / Config (`app-prompts`)**
    - PK: `client_id` (S)
    - SK: `id` (S) - 例: `system`, `func_config`, `ext_tools`
4.  **Tasks (`app-tasks`)**
    - PK: `client_id` (S)
    - SK: `name` (S)



## API 仕様

ベースURL: `https://{api_id}.execute-api.{region}.amazonaws.com`

**認証**:
- 原則として `Authorization: Bearer {id_token}` ヘッダーが必要です。
- システム間連携（Realtime API Serverなど）の場合は、Lambda側で許可された `x-client-id` ヘッダーを使用することも可能です（実装依存）。

### API 使用例 (cURL)

#### 1. 認証トークン (ID Token) の取得
Cognito ユーザープールからトークンを取得します（例: AWS CLIを使用）。

```bash
# 必要な変数をセット
CLIENT_ID="<Cognito_Client_ID>"
USERNAME="<email>"
PASSWORD="<password>"

# トークン取得
ID_TOKEN=$(aws cognito-idp initiate-auth \
  --region ap-northeast-1 \
  --auth-flow USER_PASSWORD_AUTH \
  --client-id $CLIENT_ID \
  --auth-parameters USERNAME=$USERNAME,PASSWORD=$PASSWORD \
  --query 'AuthenticationResult.IdToken' \
  --output text)

echo "Token acquired."
```

#### 2. Chat API (POST /chat)

```bash
curl -X POST "$API_BASE/chat" \
  -H "Authorization: Bearer $ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "09012345678",
    "user_text": "予約したいです"
  }'
```

#### 3. Call Logs 取得 (GET /calls)

```bash
# 電話番号でフィルタ
curl -s "$API_BASE/calls?phone=09012345678" \
  -H "Authorization: Bearer $ID_TOKEN"
```

#### 4. FAQ 一覧取得 (GET /faqs)

```bash
curl -s "$API_BASE/faqs" \
  -H "Authorization: Bearer $ID_TOKEN"
```

#### 5. Task 作成 (POST /task)

```bash
curl -X POST "$API_BASE/task" \
  -H "Authorization: Bearer $ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "タナカ_20241201_1000",
    "phone_number": "09012345678",
    "start_datetime": "2024-12-01T10:00:00",
    "request": "カット"
  }'
```

### エンドポイント一覧

#### Chat & Config
- `POST /chat`: 会話の実行
- `GET/PUT /prompt`: システムプロンプトの取得・更新
- `GET/PUT /func-config`: Function Calling 定義の取得・更新
- `GET/PUT /ext-tools`: 外部APIツール連携設定の取得・更新
- `GET /chat-logs`: Lambda 実行ログの取得 (CloudWatch)

#### Call Logs
- `GET /calls`: 通話ログ一覧（検索・フィルタリング）
- `GET /phones`: 登録済み電話番号一覧
- `POST /call`: ログの手動作成
- `GET/PUT/DELETE /call`: ログの取得・更新・削除
- `GET /recordings`: 録音一覧 (Twilio)
- `GET /recording/{sid}`: 録音データ取得 (Twilio -> Relay)
- `GET /transcription`: 録音の文字起こし (Whisper)

#### FAQ
- `GET /faqs`: 一覧取得
- `POST /faq`: 作成
- `GET/PUT/DELETE /faq/{question}`: 個別操作

#### Tasks
- `GET /tasks`: 一覧取得
- `POST /task`: 作成
- `GET/PUT/DELETE /task/{name}`: 個別操作

## ローカル開発 / テスト

`test.py` や `chat_api_test.py` は、認証なしでのアクセスを前提とした旧仕様のままの場合があります。
AWS 上の API に対してテストを行う場合は、Cognito でユーザーを作成し、IDトークンを取得してヘッダーに付与する必要があります。

または、AWS CLI で `aws-vault` 等を使用して認証済みの状態で Lambda を直接 Invoke してテストすることも可能です。

# AWS Architecture & Setup

This system is built on a serverless architecture using AWS Lambda, API Gateway, DynamoDB, and Cognito.

## Architecture Overview

```mermaid
graph TD
    Client[Web Frontend<br/>(Unified App)] -->|Auth & API| APIGW[API Gateway<br/>(HTTP API)]
    Realtime[Realtime Server<br/>(Python/Fargate)] -->|API| APIGW
    
    subgraph "AWS Cloud"
        APIGW -->|JWT Auth| Cognito[Cognito User Pool]
        
        APIGW -->|/chat| LambdaChat[Lambda: app-chat]
        APIGW -->|/calls| LambdaLogs[Lambda: app-calllogs]
        APIGW -->|/faq| LambdaFAQ[Lambda: app-faq]
        APIGW -->|/tasks| LambdaTasks[Lambda: app-tasks]
        
        LambdaChat -->|Read/Write| DDB_Logs[DynamoDB: app-logs]
        LambdaChat -->|Read| DDB_FAQ[DynamoDB: app-faq]
        LambdaChat -->|Read/Write| DDB_Prompts[DynamoDB: app-prompts]
        
        LambdaLogs -->|Read| DDB_Logs
        LambdaFAQ -->|CRUD| DDB_FAQ
        LambdaTasks -->|CRUD| DDB_Tasks[DynamoDB: app-tasks]
        
        LambdaChat -->|LLM| OpenAI[OpenAI API]
        LambdaLogs -->|Recordings| Twilio[Twilio API]
        LambdaLogs -->|Transcribe| OpenAI
    end
```

## Resources

### Authentication
- **Service**: AWS Cognito
- **User Pool**: `app-user-pool`
- **Client**: `app-web-client`
- **Attributes**: `email` (username), `custom:tenant_id` (for multi-tenancy)

### Database (DynamoDB)
All tables are multi-tenant capable, partitioned by `client_id`.

| Table Name | Primary Key (PK) | Sort Key (SK) | GSI | Description |
|---|---|---|---|---|
| **app-logs** | `client_id` | `sk` (composite) | `TsIndex` (PK:client_id, SK:ts)<br/>`CallSidIndex` (PK:call_sid) | Chat/Call history. `sk` format: `phone_number#timestamp` |
| **app-faq** | `client_id` | `question` | - | FAQ knowledge base |
| **app-prompts** | `client_id` | `id` | - | System prompts & configurations |
| **app-tasks** | `client_id` | `name` | - | Reservation/Task data |

### API (API Gateway + Lambda)
- **Base URL**: `https://km5m358bik.execute-api.ap-northeast-1.amazonaws.com` (Example)
- **Authentication**: Cognito JWT Authorizer (configured but optional for server-to-server) / `x-client-id` header for internal logic.

#### Lambda Functions
1. **app-chat** (`POST /chat`, `/prompt`, `/func-config`): Main chat logic.
2. **app-calllogs** (`/calls`, `/recordings`): Call history and recording management.
3. **app-faq** (`/faq`): FAQ CRUD.
4. **app-tasks** (`/tasks`): Task CRUD.

## Multi-tenancy Strategy

- **Data Isolation**: Application logic enforces `client_id` filters on every DB query.
- **Client Identification**:
  - **Frontend**: Extracted from Cognito ID Token (`custom:tenant_id`).
  - **Realtime Server**: Provided via `x-client-id` HTTP header.

## Setup Instructions

1. **Deploy Infrastructure**:
   ```bash
   cd chat_api/terraform
   terraform init
   terraform apply
   ```

2. **Frontend Config**:
   Update `unified_app/.env.local` with outputs from Terraform.

3. **Create User (Tenant Admin)**:
   Users must be created via AWS Console or CLI (AdminCreateUser), setting `custom:tenant_id` attribute.
   ```bash
   aws cognito-idp admin-create-user --user-pool-id <POOL_ID> --username <EMAIL> --user-attributes Name=email,Value=<EMAIL> Name=email_verified,Value=true Name="custom:tenant_id",Value="ueki"
   aws cognito-idp admin-set-user-password --user-pool-id <POOL_ID> --username <EMAIL> --password <PASSWORD> --permanent
   ```

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.2.0"
    }
  }
}

provider "aws" {
  region = var.region
}

# ==============================================================================
# Cognito (Authentication)
# ==============================================================================

resource "aws_cognito_user_pool" "main" {
  name = "app-user-pool"

  password_policy {
    minimum_length    = 8
    require_lowercase = false
    require_numbers   = false
    require_symbols   = false
    require_uppercase = false
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  
  # Custom attribute for tenant ID
  schema {
    attribute_data_type = "String"
    name                = "tenant_id"
    required            = false
    mutable             = true
    string_attribute_constraints {
      min_length = 1
      max_length = 256
    }
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  tags = {
    Project = "chat_api"
    Env     = var.env
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name = "app-web-client"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false
  
  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH"
  ]
}

# ==============================================================================
# DynamoDB Tables (Multi-tenant)
# ==============================================================================

# 1. Logs Table: app-logs (Integrated ueki-chatbot)
# PK: client_id, SK: ts#phone_number (for efficient query per client)
# GSI1: PK: client_id, SK: ts (for time-range query per client)
# GSI2: PK: call_sid (for direct lookup)
resource "aws_dynamodb_table" "app_logs" {
  name         = "app-logs"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "client_id"
  range_key = "sk"  # Composite: ts#phone_number

  attribute {
    name = "client_id"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  attribute {
    name = "ts"
    type = "S"
  }

  attribute {
    name = "call_sid"
    type = "S"
  }

  # GSI for time-based query: PK=client_id, SK=ts
  global_secondary_index {
    name            = "TsIndex"
    hash_key        = "client_id"
    range_key       = "ts"
    projection_type = "ALL"
  }

  # GSI for call_sid lookup: PK=call_sid
  global_secondary_index {
    name            = "CallSidIndex"
    hash_key        = "call_sid"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Project = "chat_api"
    Env     = var.env
  }
}

# 2. FAQ Table: app-faq (Integrated ueki-faq)
# PK: client_id, SK: question
resource "aws_dynamodb_table" "app_faq" {
  name         = "app-faq"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "client_id"
  range_key = "question"

  attribute {
    name = "client_id"
    type = "S"
  }

  attribute {
    name = "question"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Project = "chat_api"
    Env     = var.env
  }
}

# 3. Prompts Table: app-prompts (Integrated ueki-prompts)
# PK: client_id, SK: id
resource "aws_dynamodb_table" "app_prompts" {
  name         = "app-prompts"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "client_id"
  range_key = "id"

  attribute {
    name = "client_id"
    type = "S"
  }

  attribute {
    name = "id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Project = "chat_api"
    Env     = var.env
  }
}

# 4. Tasks Table: app-tasks (Integrated ueki-tasks)
# PK: client_id, SK: name
resource "aws_dynamodb_table" "app_tasks" {
  name         = "app-tasks"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "client_id"
  range_key = "name"

  attribute {
    name = "client_id"
    type = "S"
  }

  attribute {
    name = "name"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Project = "chat_api"
    Env     = var.env
  }
}

# ==============================================================================
# IAM & Lambda
# ==============================================================================

# Common IAM Role for Lambdas
resource "aws_iam_role" "app_lambda_role" {
  name = "app-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow",
      Action = "sts:AssumeRole",
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "app_lambda_policy" {
  name = "app-lambda-policy"
  role = aws_iam_role.app_lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = ["dynamodb:*"],
        Resource = [
          aws_dynamodb_table.app_logs.arn,
          "${aws_dynamodb_table.app_logs.arn}/index/*",
          aws_dynamodb_table.app_faq.arn,
          aws_dynamodb_table.app_prompts.arn,
          aws_dynamodb_table.app_tasks.arn
        ]
      },
      {
        Effect = "Allow",
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"],
        Resource = ["*"]
      },
      {
        Effect = "Allow",
        Action = ["secretsmanager:GetSecretValue"],
        Resource = ["arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:UEKI_OPENAI_APIKEY*"]
      },
      {
        Effect = "Allow",
        Action = ["cognito-idp:GetUser"],
        Resource = [aws_cognito_user_pool.main.arn]
      }
    ]
  })
}

# 1. Chat Lambda (ueki_chat -> app_chat)
data "archive_file" "ueki_chat_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/ueki_chat"
  output_path = "${path.module}/.build/ueki_chat.zip"
}

resource "aws_lambda_function" "app_chat" {
  function_name = "app-chat"
  role          = aws_iam_role.app_lambda_role.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  filename      = data.archive_file.ueki_chat_zip.output_path
  source_code_hash = filebase64sha256(data.archive_file.ueki_chat_zip.output_path)
  timeout       = 10
  environment {
    variables = {
      CALL_LOGS_TABLE_NAME = aws_dynamodb_table.app_logs.name
      FAQ_TABLE_NAME       = aws_dynamodb_table.app_faq.name
      PROMPTS_TABLE_NAME   = aws_dynamodb_table.app_prompts.name
      TASKS_TABLE_NAME     = aws_dynamodb_table.app_tasks.name
      COGNITO_USER_POOL_ID = aws_cognito_user_pool.main.id
      OPENAI_API_KEY       = var.openai_api_key
      OPENAI_SECRET_NAME   = "UEKI_OPENAI_APIKEY"
    }
  }
}

# 2. Call Logs Lambda (ueki_calllogs -> app_calllogs)
data "archive_file" "ueki_calllogs_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/ueki_calllogs"
  output_path = "${path.module}/.build/ueki_calllogs.zip"
}

resource "aws_lambda_function" "app_calllogs" {
  function_name = "app-calllogs"
  role          = aws_iam_role.app_lambda_role.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  filename      = data.archive_file.ueki_calllogs_zip.output_path
  source_code_hash = filebase64sha256(data.archive_file.ueki_calllogs_zip.output_path)
  timeout       = 30
  environment {
    variables = {
      CALL_LOGS_TABLE_NAME = aws_dynamodb_table.app_logs.name
      TWILIO_ACCOUNT_SID   = var.twilio_account_sid
      TWILIO_AUTH_TOKEN    = var.twilio_auth_token
      OPENAI_SECRET_NAME   = "UEKI_OPENAI_APIKEY"
      COGNITO_USER_POOL_ID = aws_cognito_user_pool.main.id
    }
  }
}

# 3. FAQ Lambda (ueki_faq -> app_faq)
data "archive_file" "ueki_faq_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/ueki_faq"
  output_path = "${path.module}/.build/ueki_faq.zip"
}

resource "aws_lambda_function" "app_faq" {
  function_name = "app-faq"
  role          = aws_iam_role.app_lambda_role.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  filename      = data.archive_file.ueki_faq_zip.output_path
  source_code_hash = filebase64sha256(data.archive_file.ueki_faq_zip.output_path)
  environment {
    variables = {
      FAQ_TABLE_NAME       = aws_dynamodb_table.app_faq.name
      COGNITO_USER_POOL_ID = aws_cognito_user_pool.main.id
    }
  }
}

# 4. Tasks Lambda (ueki_tasks -> app_tasks)
data "archive_file" "ueki_tasks_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/ueki_tasks"
  output_path = "${path.module}/.build/ueki_tasks.zip"
}

resource "aws_lambda_function" "app_tasks" {
  function_name = "app-tasks"
  role          = aws_iam_role.app_lambda_role.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  filename      = data.archive_file.ueki_tasks_zip.output_path
  source_code_hash = filebase64sha256(data.archive_file.ueki_tasks_zip.output_path)
  environment {
    variables = {
      TASKS_TABLE_NAME     = aws_dynamodb_table.app_tasks.name
      COGNITO_USER_POOL_ID = aws_cognito_user_pool.main.id
    }
  }
}

# ==============================================================================
# API Gateway (Auth Integration)
# ==============================================================================

data "aws_caller_identity" "current" {}

resource "aws_apigatewayv2_api" "app" {
  name          = "app-api"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allow_headers = ["*", "content-type", "authorization", "x-client-id"]
    expose_headers = ["*"]
    max_age = 3600
  }
}

# Cognito Authorizer
resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.app.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "cognito-authorizer"

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.web.id]
    issuer   = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
  }
}

resource "aws_apigatewayv2_stage" "app" {
  api_id      = aws_apigatewayv2_api.app.id
  name        = "$default"
  auto_deploy = true
}

# --- Integrations ---

resource "aws_apigatewayv2_integration" "app_chat" {
  api_id           = aws_apigatewayv2_api.app.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.app_chat.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "app_calllogs" {
  api_id           = aws_apigatewayv2_api.app.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.app_calllogs.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "app_faq" {
  api_id           = aws_apigatewayv2_api.app.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.app_faq.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "app_tasks" {
  api_id           = aws_apigatewayv2_api.app.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.app_tasks.invoke_arn
  payload_format_version = "2.0"
}

# --- Routes (Protected) ---
# Most routes require authentication (Cognito)

# Chat & Config Routes
resource "aws_apigatewayv2_route" "routes_chat" {
  for_each = toset([
    "POST /chat", 
    "GET /prompt", "PUT /prompt", 
    "GET /func-config", "PUT /func-config",
    "GET /ext-tools", "PUT /ext-tools",
    "GET /chat-logs"
  ])
  api_id    = aws_apigatewayv2_api.app.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.app_chat.id}"
  # authorizer_id = aws_apigatewayv2_authorizer.cognito.id
  # authorization_type = "JWT"
  # Note: Temporarily disabled auth for migration/testing, or set enabling it step-by-step
  # To enable auth: uncomment above lines. 
  # However, for realtime-api access (server-to-server), we might need IAM auth or API Key, 
  # OR simple header check in Lambda. 
  # For now, let's keep it open or handle auth in Lambda for flexibility with server-to-server.
}

# Call Logs Routes
resource "aws_apigatewayv2_route" "routes_calllogs" {
  for_each = toset([
    "GET /calls", "GET /phones", "POST /call", "GET /call", "PUT /call", "DELETE /call",
    "GET /recordings", "GET /recording/{proxy+}", "GET /transcription"
  ])
  api_id    = aws_apigatewayv2_api.app.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.app_calllogs.id}"
}

# FAQ Routes
resource "aws_apigatewayv2_route" "routes_faq" {
  for_each = toset([
    "GET /faqs", "POST /faq", "GET /faq/{proxy+}", "PUT /faq/{proxy+}", "DELETE /faq/{proxy+}"
  ])
  api_id    = aws_apigatewayv2_api.app.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.app_faq.id}"
}

# Task Routes
resource "aws_apigatewayv2_route" "routes_tasks" {
  for_each = toset([
    "GET /tasks", "POST /task", "GET /task/{proxy+}", "PUT /task/{proxy+}", "DELETE /task/{proxy+}"
  ])
  api_id    = aws_apigatewayv2_api.app.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.app_tasks.id}"
}

# Permissions
resource "aws_lambda_permission" "perm_chat" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app_chat.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.app.execution_arn}/*/*"
}
resource "aws_lambda_permission" "perm_calllogs" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app_calllogs.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.app.execution_arn}/*/*"
}
resource "aws_lambda_permission" "perm_faq" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app_faq.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.app.execution_arn}/*/*"
}
resource "aws_lambda_permission" "perm_tasks" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app_tasks.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.app.execution_arn}/*/*"
}

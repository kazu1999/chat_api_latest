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

resource "aws_dynamodb_table" "call_logs" {
  name         = var.table_name
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "phone_number"
  range_key = "ts"

  attribute {
    name = "phone_number"
    type = "S"
  }

  attribute {
    name = "ts"
    type = "S"
  }

  # For lookup by call_sid (e.g., from telephony provider logs)
  attribute {
    name = "call_sid"
    type = "S"
  }

  global_secondary_index {
    name            = "callSidIndex"
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

# FAQ table: ueki-faq (PK: id optional, using question as PK for simplicity)
resource "aws_dynamodb_table" "faq" {
  name         = "ueki-faq"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "question"

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

# Prompts table: ueki-prompts (PK: id)
resource "aws_dynamodb_table" "prompts" {
  name         = "ueki-prompts"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "id"

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

# Tasks table: ueki-tasks (PK: name)
resource "aws_dynamodb_table" "tasks" {
  name         = "ueki-tasks"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "name"

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

# IAM role for Lambda
data "aws_iam_policy_document" "ueki_lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ueki_lambda_role" {
  name = "ueki-faq-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = "sts:AssumeRole",
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "ueki_lambda_policy" {
  name = "ueki-faq-lambda-policy"
  role = aws_iam_role.ueki_lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = ["dynamodb:*"],
        Resource = [aws_dynamodb_table.faq.arn]
      },
      {
        Effect   = "Allow",
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
        Resource = ["*"]
      }
    ]
  })
}

# Package-less Lambda pointing to inline zip via local file
data "archive_file" "ueki_faq_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/ueki_faq"
  output_path = "${path.module}/.build/ueki_faq.zip"
}

resource "aws_lambda_function" "ueki_faq" {
  function_name = "ueki-faq"
  role          = aws_iam_role.ueki_lambda_role.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  filename      = data.archive_file.ueki_faq_zip.output_path

  environment {
    variables = {
      FAQ_TABLE_NAME = aws_dynamodb_table.faq.name
    }
  }
}

# Call Logs Lambda
data "archive_file" "ueki_calllogs_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/ueki_calllogs"
  output_path = "${path.module}/.build/ueki_calllogs.zip"
}

############################
# Chat Lambda (/chat)
data "archive_file" "ueki_chat_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/ueki_chat"
  output_path = "${path.module}/.build/ueki_chat.zip"
}

resource "aws_iam_role" "ueki_chat_role" {
  name = "ueki-chat-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow",
      Action = "sts:AssumeRole",
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "ueki_chat_policy" {
  name = "ueki-chat-lambda-policy"
  role = aws_iam_role.ueki_chat_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = ["dynamodb:*"],
        Resource = [aws_dynamodb_table.call_logs.arn, aws_dynamodb_table.faq.arn, aws_dynamodb_table.prompts.arn, aws_dynamodb_table.tasks.arn]
      },
      {
        Effect = "Allow",
        Action = [
          "logs:FilterLogEvents",
          "logs:GetLogEvents",
          "logs:DescribeLogStreams"
        ],
        Resource = [
          "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/ueki-chat:*"
        ]
      },
      {
        Effect = "Allow",
        Action = ["secretsmanager:GetSecretValue"],
        Resource = ["arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:UEKI_OPENAI_APIKEY*"]
      },
      {
        Effect = "Allow",
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
        Resource = ["*"]
      }
    ]
  })
}

resource "aws_lambda_function" "ueki_chat" {
  function_name = "ueki-chat"
  role          = aws_iam_role.ueki_chat_role.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  filename      = data.archive_file.ueki_chat_zip.output_path
  source_code_hash = filebase64sha256(data.archive_file.ueki_chat_zip.output_path)
  timeout       = 10
  environment {
    variables = {
      CALL_LOGS_TABLE_NAME = aws_dynamodb_table.call_logs.name
      FAQ_TABLE_NAME       = aws_dynamodb_table.faq.name
      PROMPTS_TABLE_NAME   = aws_dynamodb_table.prompts.name
      OPENAI_API_KEY       = var.openai_api_key
      OPENAI_SECRET_NAME   = "UEKI_OPENAI_APIKEY"
      TASKS_TABLE_NAME     = aws_dynamodb_table.tasks.name
    }
  }
}

data "aws_caller_identity" "current" {}

resource "aws_apigatewayv2_integration" "ueki_chat" {
  api_id                 = aws_apigatewayv2_api.ueki.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ueki_chat.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "ueki_chat_route" {
  api_id    = aws_apigatewayv2_api.ueki.id
  route_key = "POST /chat"
  target    = "integrations/${aws_apigatewayv2_integration.ueki_chat.id}"
}

# Prompt routes served by ueki-chat Lambda
resource "aws_apigatewayv2_route" "ueki_chat_prompt_routes" {
  for_each = toset(["GET /prompt", "PUT /prompt"])
  api_id    = aws_apigatewayv2_api.ueki.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.ueki_chat.id}"
}

# Function-config routes served by ueki-chat Lambda
resource "aws_apigatewayv2_route" "ueki_chat_funccfg_routes" {
  for_each = toset(["GET /func-config", "PUT /func-config", "GET /chat-logs", "GET /ext-tools", "PUT /ext-tools"])
  api_id    = aws_apigatewayv2_api.ueki.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.ueki_chat.id}"
}

resource "aws_lambda_permission" "ueki_chat_allow_apigw" {
  statement_id  = "AllowAPIGatewayInvokeChat"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ueki_chat.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ueki.execution_arn}/*/*"
}
resource "aws_iam_role" "ueki_calllogs_role" {
  name = "ueki-calllogs-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow",
      Action = "sts:AssumeRole",
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "ueki_calllogs_policy" {
  name = "ueki-calllogs-lambda-policy"
  role = aws_iam_role.ueki_calllogs_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = ["dynamodb:*"],
        Resource = [
          aws_dynamodb_table.call_logs.arn,
          "${aws_dynamodb_table.call_logs.arn}/index/*"
        ]
      },
      {
        Effect = "Allow",
        Action = ["secretsmanager:GetSecretValue"],
        Resource = [
          "arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:UEKI_OPENAI_APIKEY*"
        ]
      },
      {
        Effect = "Allow",
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
        Resource = ["*"]
      }
    ]
  })
}

resource "aws_lambda_function" "ueki_calllogs" {
  function_name = "ueki-calllogs"
  role          = aws_iam_role.ueki_calllogs_role.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  filename      = data.archive_file.ueki_calllogs_zip.output_path
  source_code_hash = filebase64sha256(data.archive_file.ueki_calllogs_zip.output_path)
  timeout       = 30
  environment {
    variables = {
      CALL_LOGS_TABLE_NAME = aws_dynamodb_table.call_logs.name
      TWILIO_ACCOUNT_SID   = var.twilio_account_sid
      TWILIO_AUTH_TOKEN    = var.twilio_auth_token
      OPENAI_SECRET_NAME   = "UEKI_OPENAI_APIKEY"
    }
  }
}

# Tasks Lambda
resource "aws_iam_role" "ueki_tasks_role" {
  name = "ueki-tasks-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow",
      Action = "sts:AssumeRole",
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "ueki_tasks_policy" {
  name = "ueki-tasks-lambda-policy"
  role = aws_iam_role.ueki_tasks_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = ["dynamodb:*"],
        Resource = [aws_dynamodb_table.tasks.arn]
      },
      {
        Effect = "Allow",
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
        Resource = ["*"]
      }
    ]
  })
}

data "archive_file" "ueki_tasks_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/ueki_tasks"
  output_path = "${path.module}/.build/ueki_tasks.zip"
}

resource "aws_lambda_function" "ueki_tasks" {
  function_name = "ueki-tasks"
  role          = aws_iam_role.ueki_tasks_role.arn
  handler       = "handler.handler"
  runtime       = "python3.11"
  filename      = data.archive_file.ueki_tasks_zip.output_path
  source_code_hash = filebase64sha256(data.archive_file.ueki_tasks_zip.output_path)
  environment {
    variables = {
      TASKS_TABLE_NAME = aws_dynamodb_table.tasks.name
    }
  }
}

resource "aws_apigatewayv2_integration" "ueki_tasks" {
  api_id                 = aws_apigatewayv2_api.ueki.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ueki_tasks.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "ueki_tasks_routes" {
  for_each = toset(["GET /tasks", "POST /task", "GET /task/{proxy+}", "PUT /task/{proxy+}", "DELETE /task/{proxy+}"])
  api_id    = aws_apigatewayv2_api.ueki.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.ueki_tasks.id}"
}

resource "aws_lambda_permission" "ueki_tasks_allow_apigw" {
  statement_id  = "AllowAPIGatewayInvokeTasks"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ueki_tasks.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ueki.execution_arn}/*/*"
}

resource "aws_apigatewayv2_integration" "ueki_calllogs" {
  api_id                 = aws_apigatewayv2_api.ueki.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ueki_calllogs.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "ueki_calls_routes" {
  for_each = toset([
    "GET /calls",
    "GET /phones",
    "POST /call",
    "GET /call",
    "PUT /call",
    "DELETE /call",
    "GET /recordings",
    "GET /recording/{proxy+}",
    "GET /transcription"
  ])
  api_id    = aws_apigatewayv2_api.ueki.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.ueki_calllogs.id}"
}

resource "aws_lambda_permission" "ueki_calllogs_allow_apigw" {
  statement_id  = "AllowAPIGatewayInvokeCallLogs"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ueki_calllogs.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ueki.execution_arn}/*/*"
}

############################
# Realtime Lambda (OpenAI Realtime API + Twilio)
# (Temporarily removed by request)

# HTTP API Gateway (API Gateway v2)
resource "aws_apigatewayv2_api" "ueki" {
  name          = "ueki"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allow_headers = ["*", "content-type"]
    expose_headers = ["*"]
    max_age = 3600
  }
}

resource "aws_apigatewayv2_integration" "ueki_faq" {
  api_id                 = aws_apigatewayv2_api.ueki.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ueki_faq.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "ueki_routes" {
  for_each = toset(["GET /faqs", "POST /faq", "GET /faq/{proxy+}", "PUT /faq/{proxy+}", "DELETE /faq/{proxy+}"])
  api_id    = aws_apigatewayv2_api.ueki.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.ueki_faq.id}"
}

resource "aws_apigatewayv2_stage" "ueki" {
  api_id      = aws_apigatewayv2_api.ueki.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "ueki_allow_apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ueki_faq.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ueki.execution_arn}/*/*"
}


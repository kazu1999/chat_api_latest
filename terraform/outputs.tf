output "app_logs_table" {
  value       = aws_dynamodb_table.app_logs.name
  description = "Application Logs DynamoDB table"
}

output "app_faq_table" {
  value       = aws_dynamodb_table.app_faq.name
  description = "Application FAQ DynamoDB table"
}

output "app_prompts_table" {
  value       = aws_dynamodb_table.app_prompts.name
  description = "Application Prompts DynamoDB table"
}

output "app_tasks_table" {
  value       = aws_dynamodb_table.app_tasks.name
  description = "Application Tasks DynamoDB table"
}

output "region" {
  value       = var.region
  description = "AWS region"
}

output "api_endpoint" {
  value       = aws_apigatewayv2_api.app.api_endpoint
  description = "HTTP API endpoint"
}

output "cognito_user_pool_id" {
  value       = aws_cognito_user_pool.main.id
  description = "Cognito User Pool ID"
}

output "cognito_client_id" {
  value       = aws_cognito_user_pool_client.web.id
  description = "Cognito Client ID (Web)"
}

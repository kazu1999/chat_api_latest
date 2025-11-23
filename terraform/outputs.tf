output "table_name" {
  value       = aws_dynamodb_table.call_logs.name
  description = "Created DynamoDB table name"
}

output "region" {
  value       = var.region
  description = "AWS region"
}

output "faq_table_name" {
  value       = aws_dynamodb_table.faq.name
  description = "Created FAQ DynamoDB table name"
}

output "ueki_api_endpoint" {
  value       = aws_apigatewayv2_api.ueki.api_endpoint
  description = "UEKI HTTP API endpoint"
}

// Realtime function URL output removed (temporarily)


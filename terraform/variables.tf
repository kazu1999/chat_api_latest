variable "region" {
  type        = string
  description = "AWS region"
  default     = "ap-northeast-1"
}

variable "table_name" {
  type        = string
  description = "DynamoDB table name"
  default     = "ueki-chatbot"
}

variable "env" {
  type        = string
  description = "Environment tag"
  default     = "dev"
}

variable "openai_api_key" {
  type        = string
  description = "OpenAI API Key (project key)"
  sensitive   = true
  default     = ""
}

variable "openai_webhook_secret" {
  type        = string
  description = "OpenAI Webhook Secret for Realtime API"
  sensitive   = true
  default     = ""
}

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


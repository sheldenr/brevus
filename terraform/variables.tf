# Terraform Input Variables

variable "location" {
  type        = string
  default     = "eastus"
  description = "The Azure region to deploy all resources into."
}

variable "project_name" {
  type        = string
  default     = "brevus"
  description = "A naming prefix for resources to maintain uniformity."
}

variable "environment" {
  type        = string
  default     = "dev"
  description = "Deployment environment identifier (e.g. dev, qa, prod)."
}

variable "notification_email_recipient" {
  type        = string
  default     = "admin@example.com"
  description = "The email address to receive secure file scrub notifications."
}

variable "notification_email_sender" {
  type        = string
  default     = "donotreply@example.com"
  description = "The verified sender email address in Azure Communication Services."
}

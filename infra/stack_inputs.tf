variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "name_prefix" {
  description = "Resource name prefix (project slug only; never a personal name or ID)."
  type        = string
  default     = "faultlab"
}

variable "tracing_mode" {
  type    = string
  default = "Active"
  validation {
    condition     = contains(["Active", "PassThrough"], var.tracing_mode)
    error_message = "tracing_mode must be Active or PassThrough."
  }
}

variable "log_level" {
  type    = string
  default = "ERROR"
}

variable "sampling_fixed_rate" {
  type    = number
  default = 0.05
}

variable "sampling_reservoir" {
  type    = number
  default = 1
}

variable "client_timeout_ms" {
  type    = number
  default = 1000
}

variable "memory_mb" {
  type    = number
  default = 256
}

variable "layer_package" {
  type    = string
  default = "../build/faultlab-layer.zip"
}

variable "orders_api_package" {
  type    = string
  default = "../build/orders-api.zip"
}

variable "inventory_package" {
  type    = string
  default = "../build/inventory.zip"
}

variable "payments_package" {
  type    = string
  default = "../build/payments.zip"
}

variable "notifications_package" {
  type    = string
  default = "../build/notifications.zip"
}

variable "alert_email" {
  description = "Empty = no budget resource."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  type    = number
  default = 20
}

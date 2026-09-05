variable "project_id" {
  description = "Existing Google Cloud project ID that will own Runtime v2 deployment bootstrap resources."
  type        = string

  validation {
    condition     = length(trimspace(var.project_id)) > 0
    error_message = "project_id must be non-empty."
  }
}

variable "region" {
  description = "Primary Runtime v2 region."
  type        = string
  default     = "asia-east1"
}

variable "state_bucket_name" {
  description = "Globally unique Cloud Storage bucket name for Runtime v2 Terraform state."
  type        = string

  validation {
    condition     = length(trimspace(var.state_bucket_name)) >= 3
    error_message = "state_bucket_name must be explicitly supplied."
  }
}

variable "state_bucket_location" {
  description = "Cloud Storage location for Terraform state."
  type        = string
  default     = "ASIA-EAST1"
}

variable "state_prefix" {
  description = "Stable GCS backend prefix for the Runtime v2 production state."
  type        = string
  default     = "hao-runtime-v2/production"

  validation {
    condition     = length(trimspace(var.state_prefix)) > 0
    error_message = "state_prefix must be non-empty."
  }
}

variable "workload_identity_pool_id" {
  description = "Workload Identity Pool ID dedicated to Runtime v2 GitHub Actions."
  type        = string
  default     = "hao-runtime-v2-github"
}

variable "workload_identity_provider_id" {
  description = "OIDC provider ID restricted to the shared Runtime v2 EXP deployment branch."
  type        = string
  default     = "shared-exp"
}

variable "deployment_service_account_id" {
  description = "Service account ID impersonated by the authorized GitHub Actions deployment workflow."
  type        = string
  default     = "hao-runtime-v2-deployer"
}

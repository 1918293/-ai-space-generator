variable "project_id" {
  description = "Existing Google Cloud project ID. This candidate never creates or bills a project."
  type        = string

  validation {
    condition     = length(trimspace(var.project_id)) > 0
    error_message = "project_id must be non-empty."
  }
}

variable "region" {
  description = "Runtime v2 region."
  type        = string
  default     = "asia-east1"
}

variable "name_prefix" {
  description = "Stable prefix for Runtime v2 resources."
  type        = string
  default     = "hao-runtime-v2"
}

variable "runtime_image" {
  description = "Immutable Runtime image reference. Must use an OCI sha256 digest."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-fA-F]{64}$", var.runtime_image))
    error_message = "runtime_image must end with @sha256:<64 hex chars>."
  }
}

variable "otel_collector_image" {
  description = "Immutable OTel Collector image that contains deploy/otel-collector.yaml."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-fA-F]{64}$", var.otel_collector_image))
    error_message = "otel_collector_image must end with @sha256:<64 hex chars>."
  }
}

variable "otel_exporter_otlp_endpoint" {
  description = "Upstream OTLP/HTTP endpoint consumed by the collector sidecar."
  type        = string

  validation {
    condition     = can(regex("^https://", var.otel_exporter_otlp_endpoint))
    error_message = "otel_exporter_otlp_endpoint must use HTTPS."
  }
}

variable "release_id" {
  type        = string
  description = "Immutable Runtime release ID."
}

variable "deployment_id" {
  type        = string
  description = "Release-group/deployment identity shared by API and Worker."
}

variable "temporal_worker_version" {
  type        = string
  description = "Immutable Temporal Worker deployment/build ID."
}

variable "public_mcp_url" {
  type        = string
  description = "Final public OAuth resource URL ending in /mcp."

  validation {
    condition     = can(regex("^https://.+/mcp$", var.public_mcp_url))
    error_message = "public_mcp_url must be HTTPS and end in /mcp."
  }
}

variable "mcp_allowed_hosts" {
  type        = string
  description = "Comma-separated allowed Host values for Runtime MCP."
}

variable "mcp_allowed_origins" {
  type        = string
  description = "Comma-separated allowed Origin values; may be empty."
  default     = ""
}

variable "mcp_request_state_audience" {
  type        = string
  description = "Audience bound into MCP request-state tokens."
  default     = "hao-system-control"
}

variable "oauth_issuer_url" {
  type        = string
  description = "Production OAuth issuer."
}

variable "oauth_jwks_url" {
  type        = string
  description = "Production JWKS endpoint."
}

variable "expected_hao_subject" {
  type        = string
  description = "Expected authenticated Hao subject."
  sensitive   = false
}

variable "attestation_key_id" {
  type        = string
  description = "Current completion-attestation signing key identifier."
}

variable "initial_task" {
  type        = string
  description = "Only used to seed a brand-new empty operational-state database."
}

variable "sheets_targets_json" {
  type        = string
  description = "Deployment-owned HAO_SHEETS_TARGETS_JSON."
  sensitive   = true
}

variable "task_policies_json" {
  type        = string
  description = "Deployment-owned HAO_TASK_POLICIES_JSON."
  sensitive   = true
}

variable "parent_task_plans_json" {
  type        = string
  description = "Deployment-owned HAO_PARENT_TASK_PLANS_JSON."
  sensitive   = true
}

variable "temporal_endpoint" {
  type        = string
  description = "Temporal Cloud endpoint."
}

variable "temporal_namespace" {
  type        = string
  description = "Temporal Cloud namespace."
}

variable "temporal_task_queue" {
  type        = string
  description = "Runtime task queue."
  default     = "hao-runtime-v2"
}

variable "temporal_api_key_version" {
  type        = string
  description = "Existing numeric Secret Manager version for the Temporal API key."

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.temporal_api_key_version))
    error_message = "temporal_api_key_version must be a positive numeric version."
  }
}

variable "attestation_secret_version" {
  type        = string
  description = "Existing numeric Secret Manager version for current attestation signing secret."

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.attestation_secret_version))
    error_message = "attestation_secret_version must be a positive numeric version."
  }
}

variable "mcp_request_state_keys_version" {
  type        = string
  description = "Existing numeric Secret Manager version for MCP request-state keys."

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.mcp_request_state_keys_version))
    error_message = "mcp_request_state_keys_version must be a positive numeric version."
  }
}

variable "database_url_secret_version" {
  type        = string
  description = "Existing numeric Secret Manager version containing the password-bearing PostgreSQL URL."

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.database_url_secret_version))
    error_message = "database_url_secret_version must be a positive numeric version."
  }
}

variable "previous_attestation_keys_secret_version" {
  type        = string
  description = "Optional existing numeric Secret Manager version containing previous verification-key JSON."
  default     = null

  validation {
    condition = (
      var.previous_attestation_keys_secret_version == null ||
      can(regex("^[1-9][0-9]*$", var.previous_attestation_keys_secret_version))
    )
    error_message = "previous_attestation_keys_secret_version must be null or a positive numeric version."
  }
}

variable "cloud_sql_tier" {
  type        = string
  description = "Explicit Cloud SQL machine tier. No default because this is cost-sensitive."
}

variable "cloud_sql_availability_type" {
  type        = string
  description = "ZONAL or REGIONAL. Explicit because cost/reliability trade-off requires deployment authorization."

  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.cloud_sql_availability_type)
    error_message = "cloud_sql_availability_type must be ZONAL or REGIONAL."
  }
}

variable "cloud_sql_database_version" {
  type        = string
  description = "Cloud SQL PostgreSQL engine version."
  default     = "POSTGRES_17"
}

variable "cloud_sql_transaction_log_retention_days" {
  type        = number
  description = "PITR transaction-log retention."
  default     = 3

  validation {
    condition     = var.cloud_sql_transaction_log_retention_days >= 1
    error_message = "cloud_sql_transaction_log_retention_days must be at least 1."
  }
}

variable "network_cidr" {
  type        = string
  description = "Direct VPC subnet CIDR for Cloud Run resources."
  default     = "10.30.0.0/24"
}

variable "private_service_prefix_length" {
  type        = number
  description = "Prefix length reserved for private service networking."
  default     = 16
}

variable "api_cpu" {
  type    = string
  default = "1"
}

variable "api_memory" {
  type    = string
  default = "512Mi"
}

variable "api_min_instances" {
  type    = number
  default = 0
}

variable "api_max_instances" {
  type    = number
  default = 3
}

variable "worker_cpu" {
  type    = string
  default = "1"
}

variable "worker_memory" {
  type    = string
  default = "512Mi"
}

variable "worker_instance_count" {
  type        = number
  description = "Manual non-zero Worker Pool capacity."
  default     = 1

  validation {
    condition     = var.worker_instance_count >= 1
    error_message = "worker_instance_count must be at least 1."
  }
}

variable "enable_runtime_workloads" {
  type        = bool
  description = "False by default. Set true only after authorized secret-version and DB-user bootstrap is complete."
  default     = false
}

variable "allow_public_mcp_invoker" {
  type        = bool
  description = "False by default. Enabling public Cloud Run invocation is a separate exposure/authorization gate."
  default     = false
}

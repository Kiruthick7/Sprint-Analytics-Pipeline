# Jira Daily Report - Terraform Configuration
# Deploys Cloud Function (Gen2) + Cloud Scheduler for Monday & Thursday 9 AM IST
# Features: Confluence API v2, optimized caching, comprehensive error handling, performance logging

terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP Region"
  type        = string
  default     = "us-central1"
}

variable "function_name" {
  description = "Name of the Cloud Function"
  type        = string
  default     = "jira-daily-report"
}

variable "schedule" {
  description = "Cron schedule for Cloud Scheduler (default: daily Mon-Sat 7 AM IST)"
  type        = string
  default     = "30 1 * * 1-6"
}

variable "time_zone" {
  description = "Timezone for the scheduler"
  type        = string
  default     = "Asia/Kolkata"
}

# Environment variables - pass as TF_VAR_* or use terraform.tfvars
variable "jira_user_name" {
  description = "Jira username/email"
  type        = string
  sensitive   = true
}

variable "jira_token" {
  description = "Jira API token"
  type        = string
  sensitive   = true
}

variable "teams_webhook" {
  description = "Teams incoming webhook URL for report link (shared channel)"
  type        = string
  sensitive   = true
}

variable "confluence_base_url" {
  description = "Confluence base URL for REST API v2 (e.g. https://your-site.atlassian.net/wiki)"
  type        = string
  default     = ""
}

variable "confluence_user" {
  description = "Confluence user email"
  type        = string
  default     = ""
  sensitive   = true
}

variable "confluence_token" {
  description = "Confluence API token"
  type        = string
  default     = ""
  sensitive   = true
}

variable "confluence_space_key" {
  description = "Confluence space key"
  type        = string
  default     = ""
}

variable "llm_provider" {
  description = "LLM provider: groq or gemini"
  type        = string
  default     = "groq"
}

variable "groq_api_key" {
  description = "Groq API key (when llm_provider=groq)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "gemini_api_key" {
  description = "Gemini/Google AI API key (when llm_provider=gemini)"
  type        = string
  default     = ""
  sensitive   = true
}

# Create a storage bucket for Cloud Function source code
resource "google_storage_bucket" "function_bucket" {
  name     = "${var.project_id}-${var.function_name}-source"
  location = var.region

  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
}

# Archive source code
data "archive_file" "function_source" {
  type        = "zip"
  source_dir  = "${path.module}"
  output_path = "${path.module}/.terraform/function-source.zip"

  excludes = [
    ".git",
    ".terraform",
    "*.tfstate",
    "*.tfvars",
    ".env",
    "old",
    "readme",
    "*.md",
    "deploy.sh"
  ]
}

# Upload source code to bucket
resource "google_storage_bucket_object" "function_source" {
  name   = "function-source-${data.archive_file.function_source.output_md5}.zip"
  bucket = google_storage_bucket.function_bucket.name
  source = data.archive_file.function_source.output_path
}

# Create Cloud Function (Gen 2)
# Features: Confluence API v2, consolidated caching, optimized error handling
resource "google_cloudfunctions2_function" "jira_report_function" {
  name        = var.function_name
  location    = var.region
  description = "Daily Jira Status Report Generator with Developer Lookup"

  build_config {
    runtime     = "python311"
    entry_point = "generate_and_send_report"

    source {
      storage_source {
        bucket = google_storage_bucket.function_bucket.name
        object = google_storage_bucket_object.function_source.name
      }
    }
  }

  service_config {
    max_instance_count    = 3
    min_instance_count    = 0
    available_memory      = "512M"
    timeout_seconds       = 540
    max_instance_request_concurrency = 1

    environment_variables = {
      JIRA_USER_NAME        = var.jira_user_name
      JIRA_TOKEN            = var.jira_token
      TEAMS_WEBHOOK         = var.teams_webhook
      CONFLUENCE_BASE_URL   = var.confluence_base_url
      CONFLUENCE_USER       = var.confluence_user
      CONFLUENCE_TOKEN      = var.confluence_token
      CONFLUENCE_SPACE_KEY  = var.confluence_space_key
      LLM_PROVIDER         = var.llm_provider
      GROQ_API_KEY         = var.groq_api_key
      GEMINI_API_KEY       = var.gemini_api_key
    }

    ingress_settings = "ALLOW_ALL"
    all_traffic_on_latest_revision = true
  }
}

# Make the function publicly accessible
resource "google_cloudfunctions2_function_iam_member" "invoker" {
  project        = google_cloudfunctions2_function.jira_report_function.project
  location       = google_cloudfunctions2_function.jira_report_function.location
  cloud_function = google_cloudfunctions2_function.jira_report_function.name

  role   = "roles/cloudfunctions.invoker"
  member = "allUsers"
}

# Create Cloud Scheduler job
resource "google_cloud_scheduler_job" "jira_report_scheduler" {
  name             = "${var.function_name}-scheduler"
  description      = "Triggers Jira Daily Report at 9 AM"
  schedule         = var.schedule
  time_zone        = var.time_zone
  region           = var.region
  attempt_deadline = "540s"

  http_target {
    http_method = "GET"
    uri         = google_cloudfunctions2_function.jira_report_function.service_config[0].uri

    oidc_token {
      service_account_email = google_service_account.scheduler_sa.email
    }
  }

  retry_config {
    retry_count = 3
    max_retry_duration = "300s"
    min_backoff_duration = "5s"
    max_backoff_duration = "60s"
  }
}

# Service account for Cloud Scheduler
resource "google_service_account" "scheduler_sa" {
  account_id   = "${var.function_name}-scheduler"
  display_name = "Service Account for Jira Report Scheduler"
}

# Grant Cloud Scheduler permission to invoke the function
resource "google_cloud_run_service_iam_member" "scheduler_invoker" {
  project  = google_cloudfunctions2_function.jira_report_function.project
  location = google_cloudfunctions2_function.jira_report_function.location
  service  = google_cloudfunctions2_function.jira_report_function.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_sa.email}"
}

# Outputs
output "function_uri" {
  description = "URI of the Cloud Function"
  value       = google_cloudfunctions2_function.jira_report_function.service_config[0].uri
}

output "scheduler_name" {
  description = "Name of the Cloud Scheduler job"
  value       = google_cloud_scheduler_job.jira_report_scheduler.name
}

output "function_name" {
  description = "Name of the deployed Cloud Function"
  value       = google_cloudfunctions2_function.jira_report_function.name
}

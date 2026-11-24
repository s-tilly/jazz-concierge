variable "project_id" {
  description = "Target Google Cloud project ID (e.g., your-gcp-project-id)."
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run and Secret Manager resources."
  type        = string
  default     = "us-central1"
}

variable "vertex_location" {
  description = "Vertex AI / Gemini endpoint location."
  type        = string
  default     = "global"
}

variable "service_name" {
  description = "Cloud Run service name."
  type        = string
  default     = "jazz-concierge"
}

variable "container_image" {
  description = "Container image URI in Artifact Registry (e.g., us-central1-docker.pkg.dev/your-gcp-project-id/agents/jazz-concierge:latest)."
  type        = string
}

variable "allow_public_access" {
  description = "When false (default), restricts Cloud Run to IAM-authenticated callers (compatible with Domain Restricted Sharing); when true, grants roles/run.invoker to allUsers."
  type        = bool
  default     = false
}

variable "authorized_invoker_members" {
  description = "IAM principals granted roles/run.invoker in private mode (e.g., ['user:alice@example.com'])."
  type        = list(string)
  default     = []
}

variable "profiler_model" {
  description = "Gemini 4 model routed to JazzProfilerAgent."
  type        = string
  default     = "gemini-4-hydrogen"
}

variable "musicologist_model" {
  description = "Gemini 4 model routed to JazzMusicologistAgent."
  type        = string
  default     = "gemini-4-neon"
}

variable "curator_model" {
  description = "Gemini 4 model routed to JazzCuratorAgent."
  type        = string
  default     = "gemini-4-carbon"
}

variable "min_instances" {
  description = "Minimum Cloud Run container instances."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum Cloud Run container instances."
  type        = number
  default     = 10
}

output "cloud_run_service_url" {
  description = "HTTPS endpoint URL of the deployed Jazz Concierge Cloud Run service."
  value       = google_cloud_run_v2_service.jazz_concierge.uri
}

output "service_account_email" {
  description = "Least-privilege runtime service account email."
  value       = google_service_account.app_sa.email
}

output "exposure_mode" {
  description = "Active Cloud Run access control mode."
  value       = var.allow_public_access ? "public_unauthenticated" : "private_iam_authenticated"
}

output "private_https_proxy_command" {
  description = "Local authenticated HTTPS forwarding tunnel command when allow_public_access = false."
  value       = "gcloud run services proxy ${var.service_name} --project=${var.project_id} --region=${var.region} --port=8080"
}

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.30.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  required_apis = [
    "aiplatform.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudtrace.googleapis.com",
    "logging.googleapis.com",
  ]
}

resource "google_project_service" "enabled_apis" {
  for_each           = toset(local.required_apis)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "app_sa" {
  account_id   = "${var.service_name}-sa"
  display_name = "Jazz Concierge (MilesAgent) Runtime Service Account"
  depends_on   = [google_project_service.enabled_apis]
}

resource "google_project_iam_member" "app_sa_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/secretmanager.secretAccessor",
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}

resource "google_secret_manager_secret" "spotify_client_id" {
  secret_id = "SPOTIFY_CLIENT_ID"
  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled_apis]
}

resource "google_secret_manager_secret" "spotify_client_secret" {
  secret_id = "SPOTIFY_CLIENT_SECRET"
  replication {
    auto {}
  }
  depends_on = [google_project_service.enabled_apis]
}

resource "google_cloud_run_v2_service" "jazz_concierge" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.app_sa.email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.container_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
      }

      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "TRUE"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.vertex_location
      }
      env {
        name  = "USE_SECRET_MANAGER"
        value = "TRUE"
      }
      env {
        name  = "PROFILER_MODEL"
        value = var.profiler_model
      }
      env {
        name  = "MUSICOLOGIST_MODEL"
        value = var.musicologist_model
      }
      env {
        name  = "CURATOR_MODEL"
        value = var.curator_model
      }
      env {
        name  = "OTEL_EXPORTER_GCP_TRACE"
        value = "TRUE"
      }
      env {
        name  = "SQLITE_DB_PATH"
        value = "/tmp/user_profile.db"
      }
    }
  }

  depends_on = [
    google_project_service.enabled_apis,
    google_project_iam_member.app_sa_roles,
  ]
}

# Mode 3 (Public Cloud Run): only provisioned when allow_public_access = true
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  count    = var.allow_public_access ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.jazz_concierge.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# Mode 2 (Private IAM Cloud Run): explicit invoker principals
resource "google_cloud_run_v2_service_iam_member" "private_invokers" {
  for_each = toset(var.authorized_invoker_members)
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.jazz_concierge.name
  role     = "roles/run.invoker"
  member   = each.value
}

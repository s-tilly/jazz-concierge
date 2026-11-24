"""Google Cloud Secret Manager integration with .env and offline fallbacks."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_PLACEHOLDER_PROJECTS = {"", "your-gcp-project-id", "<your-gcp-project-id>", "example-project"}


class SecretManagerResolver:
    """Resolves application secrets via Google Cloud Secret Manager or environment variables.

    In cloud production (`USE_SECRET_MANAGER=TRUE`), fetches latest secret versions
    from Google Cloud Secret Manager and falls back cleanly to environment variables
    in local development, CI, and offline test runs.
    """

    def __init__(
        self,
        project_id: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        raw_project = project_id if project_id is not None else os.getenv("GOOGLE_CLOUD_PROJECT", "")
        self.project_id = raw_project.strip()
        if enabled is None:
            use_sm = os.getenv("USE_SECRET_MANAGER", "FALSE").strip().upper()
            self.enabled = use_sm in {"TRUE", "1", "YES"}
        else:
            self.enabled = enabled
        self._cache: dict[str, str] = {}
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google.cloud import secretmanager

            self._client = secretmanager.SecretManagerServiceClient()
        return self._client

    def resolve(self, secret_name: str, default: str = "") -> str:
        """Resolve a secret by name with Secret Manager -> env var -> default fallback.

        Args:
            secret_name: Environment variable / Secret Manager secret identifier.
            default: Default value returned when unset or empty.

        Returns:
            Resolved non-None string secret value.
        """
        env_val = os.getenv(secret_name, "").strip()
        if not self.enabled or self.project_id.lower() in _PLACEHOLDER_PROJECTS:
            return env_val if env_val else default

        if secret_name in self._cache:
            return self._cache[secret_name]

        try:
            client = self._get_client()
            resource_name = f"projects/{self.project_id}/secrets/{secret_name}/versions/latest"
            response = client.access_secret_version(request={"name": resource_name})
            secret_value = response.payload.data.decode("utf-8").strip()
            if secret_value:
                self._cache[secret_name] = secret_value
                return secret_value
        except Exception as exc:
            logger.debug(
                "Secret Manager lookup skipped for '%s', falling back to env (%s)",
                secret_name,
                type(exc).__name__,
            )

        return env_val if env_val else default


def get_secret(secret_name: str, default: str = "") -> str:
    """Convenience helper resolving a single secret via SecretManagerResolver."""
    resolver = SecretManagerResolver()
    return resolver.resolve(secret_name, default=default)

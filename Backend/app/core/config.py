from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "Backend/.env"), env_file_encoding="utf-8"
    )

    # App
    app_name: str = "AgentGCS Backend"
    api_prefix: str = "/api"
    backend_cors_origins: str = Field(default="http://localhost:3000")

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_webhook_secret: str | None = None

    # Encryption
    encryption_master_key: str = ""

    # Claude / Anthropic
    claude_api_key: str | None = None
    anthropic_base_url: str | None = None
    anthropic_auth_token: str | None = None
    claude_model: str | None = None
    school_api_token: str | None = None

    # NotebookLM / GCP
    notebooklm_cli_path: str = "notebooklm-mcp-cli"
    notebooklm_output_dir: str = "./outputs"
    gcp_project_id: str | None = None
    google_application_credentials: str | None = None

    # Google OAuth (Drive/Calendar/Gmail)
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_oauth_redirect_uri: str | None = None
    google_oauth_access_token: str | None = None

    # Other
    claude_api_key: str | None = None

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

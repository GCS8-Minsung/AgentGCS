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
    backend_cors_origin_regex: str | None = (
        r"^https?://("
        r"localhost|127\.0\.0\.1|"
        r"192\.168\.\d+\.\d+|"
        r"10\.\d+\.\d+\.\d+|"
        r"172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+"
        r")(:\d+)?$"
    )

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_webhook_secret: str | None = None

    # Encryption
    encryption_master_key: str = ""

    # Claude / Anthropic
    claude_api_key: str | None = None
    anthropic_base_url: str = "https://claude.1000.school"
    anthropic_auth_token: str | None = None
    claude_model: str = "claude-sonnet-4-6"
    school_api_token: str | None = None
    openai_api_key: str | None = None
    openai_fallback_url: str = "https://api.openai.com/v1/chat/completions"
    openai_fallback_model: str = "gpt-5-mini"

    # NotebookLM / GCP
    notebooklm_cli_path: str = "nlm"
    notebooklm_output_dir: str = "./outputs"
    gcp_project_id: str | None = None
    google_application_credentials: str | None = None

    # Google OAuth (Drive/Calendar/Gmail)
    google_client_id: str | None = "513803184584-7sb5sp4qv68a534kvd0u3inp0ruf021r.apps.googleusercontent.com"
    google_client_secret: str | None = None
    google_oauth_redirect_uri: str | None = None
    google_oauth_access_token: str | None = None
    google_drive_output_root_name: str = "AgentGCS-output"
    google_drive_input_root_name: str = "input"
    google_drive_input_root_folder_id: str | None = None
    google_drive_output_root_folder_id: str | None = None
    google_service_account_project_id: str | None = None
    google_service_account_client_email: str | None = None

    # Chat Context (Redis)
    redis_url: str | None = None
    chat_context_ttl_seconds: int = 60 * 60 * 24
    chat_context_max_messages: int = 20
    chat_context_key_prefix: str = "agentgcs:chatctx"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "AgentGCS Backend"
    api_prefix: str = "/api"
    backend_cors_origins: str = Field(default="http://localhost:3000")

    supabase_url: str = ""
    supabase_service_role_key: str = ""

    encryption_master_key: str = ""
    claude_api_key: str | None = None
    anthropic_base_url: str | None = None
    anthropic_auth_token: str | None = None
    claude_model: str | None = None
    school_api_token: str | None = None

    notebooklm_cli_path: str = "notebooklm-mcp-cli"
    notebooklm_output_dir: str = "./outputs"
    supabase_webhook_secret: str | None = None

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

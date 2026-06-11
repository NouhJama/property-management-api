from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    # Application
    app_name: str
    app_version: str
    debug: bool

    # Database
    database_url: str

    # Security
    secret_key: str
    algorithm: str
    access_token_expire_minutes: int

    # CORS
    allowed_origins: str

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()

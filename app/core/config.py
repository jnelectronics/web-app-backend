from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, read from environment variables (SAD §5.8)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str = "development"

    DATABASE_URL: str = "postgresql+psycopg2://jn_user:jn_password@localhost:5432/jn_electronics"
    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET_KEY: str = "change-me-in-local-env"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    CLOUDINARY_URL: str | None = None
    EMAIL_PROVIDER_API_KEY: str | None = None
    PAYMENT_PROVIDER_API_KEY: str | None = None

    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "JN Electronics API"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

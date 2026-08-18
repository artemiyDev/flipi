from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    database_url: str = Field(
        default="postgresql+asyncpg://ankibot:ankibot@db:5432/ankibot",
        alias="DATABASE_URL",
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    auto_create_tables: bool = Field(default=False, alias="AUTO_CREATE_TABLES")
    auth_max_age_seconds: int = Field(default=86400, alias="AUTH_MAX_AGE_SECONDS")
    web_app_url: str = Field(default="", alias="WEB_APP_URL")
    bot_username: str = Field(default="", alias="BOT_USERNAME")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()

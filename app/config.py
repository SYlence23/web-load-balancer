from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    renderers: str = "http://localhost:5001,http://localhost:5002"
    health_interval: float = 2.0
    health_fails: int = 3
    database_url: str = "postgresql+asyncpg://postgres@localhost:5432/WEBdb"

    @property
    def renderer_urls(self) -> list[str]:
        return [url.strip().rstrip("/") for url in self.renderers.split(",") if url.strip()]


settings = Settings()

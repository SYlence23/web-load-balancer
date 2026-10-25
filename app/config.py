from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    renderers: str = "http://localhost:5001,http://localhost:5002"
    health_interval: float = 2.0
    health_fails: int = 3
    database_url: str = "postgresql+asyncpg://postgres@localhost:5432/WEBdb"
    tracker_interval: float = 1.0
    images_dir: str = "data/images"
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_expire_minutes: int = 24 * 60

    max_width: int = 1920
    max_height: int = 1080
    max_spp: int = 256
    max_complexity: int = 150_000_000
    max_active_per_user: int = 3
    max_history_per_user: int = 50

    @property
    def renderer_urls(self) -> list[str]:
        return [url.strip().rstrip("/") for url in self.renderers.split(",") if url.strip()]


settings = Settings()

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://mobipartner:mobipartner_dev@db:5432/mobipartner"
    redis_url: str = "redis://redis:6379/0"
    debug: bool = True
    usd_ars_rate_fallback: float = 1300.0
    scrape_schedule: str = "0 20 * * *"  # cron expression, default 8pm daily
    scrape_enabled: bool = True

    # Auth
    api_key: str = ""
    admin_api_key: str = ""
    cors_origins: str = "http://localhost:3000"

    # GitHub (for triggering Actions workflows)
    github_token: str = ""
    github_repo: str = "ByMofm/mobipartner"

    # Ollama image analysis
    ollama_url: str = "http://ollama:11434"
    image_analysis_enabled: bool = False
    image_analysis_model: str = "llava:13b"
    image_analysis_max_per_run: int = 50
    image_analysis_max_images: int = 4

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

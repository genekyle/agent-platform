import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://agent:agent@localhost:5432/agentos"
    observer_artifacts_dir: str = "../mcp/output"
    # Local asset store (listing photos). Stub for eventual cloud (S3) storage; empty → repo-root
    # /assets. See assets.py + assets/README.md.
    assets_dir: str = ""
    capture_server_url: str = "http://localhost:8082"
    chrome_cdp_url: str = "http://localhost:9222"
    chrome_binary_path: str = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    training_chrome_profiles_dir: str = "/tmp/agent-platform-training-chrome"
    training_chrome_port_start: int = 9322
    redis_url: str = "redis://localhost:6379/0"
    # Anthropic key for the Haiku SoM picker. Read from .env here so adding it
    # never crashes Settings; extra="ignore" also tolerates other future .env keys.
    anthropic_api_key: str = ""
    # Hard cap on AUTONOMOUS (no-human-in-the-loop) Claude spend per rolling 7 days.
    # When exceeded, the budget guard blocks further LLM calls and the loop must
    # escalate to a human. Keeps testing/runtime cost bounded. Override in .env.
    anthropic_weekly_budget_usd: float = 5.0
    # Test-account credentials for capturing logged-in states, read from the
    # GITIGNORED .env only. NEVER hardcode a real value here and NEVER log these —
    # see _login_secrets(). Use a throwaway/test account, not a primary one.
    fb_username: str = ""
    fb_password: str = ""
    indeed_username: str = ""
    indeed_password: str = ""
    gmail_username: str = ""
    gmail_password: str = ""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()

# Export the key into the process env so `anthropic.Anthropic()` (which reads
# ANTHROPIC_API_KEY from os.environ) works server-side without extra plumbing.
if settings.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

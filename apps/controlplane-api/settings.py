from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://agent:agent@localhost:5432/agentos"
    observer_artifacts_dir: str = "../mcp-mock/output"
    capture_server_url: str = "http://localhost:8082"
    chrome_cdp_url: str = "http://localhost:9222"
    chrome_binary_path: str = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    training_chrome_profiles_dir: str = "/tmp/agent-platform-training-chrome"
    training_chrome_port_start: int = 9322
    redis_url: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()

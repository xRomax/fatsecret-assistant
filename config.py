import os
from dataclasses import dataclass

@dataclass
class Settings:
    telegram_bot_token: str = os.environ["TELEGRAM_BOT_TOKEN"]
    telegram_owner_id: int = int(os.environ["TELEGRAM_OWNER_ID"])
    openai_api_key: str = os.environ["OPENAI_API_KEY"]
    fatsecret_consumer_key: str = os.environ["FATSECRET_CONSUMER_KEY"]
    fatsecret_consumer_secret: str = os.environ["FATSECRET_CONSUMER_SECRET"]
    public_base_url: str = os.environ["PUBLIC_BASE_URL"]
    data_dir: str = os.getenv("DATA_DIR", "/data")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

settings = Settings()

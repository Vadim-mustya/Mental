import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    proxyapi_key: str
    proxyapi_base_url: str
    gpt_model: str


def get_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is missing in .env")

    proxy_key = os.getenv("PROXYAPI_KEY", "").strip()
    if not proxy_key:
        raise RuntimeError("PROXYAPI_KEY is missing in .env")

    base_url = os.getenv("PROXYAPI_BASE_URL", "https://api.proxyapi.ru/openai/v1").strip()
    model = os.getenv("GPT_MODEL", "gpt-5").strip()

    return Settings(
        bot_token=bot_token,
        proxyapi_key=proxy_key,
        proxyapi_base_url=base_url,
        gpt_model=model,
    )

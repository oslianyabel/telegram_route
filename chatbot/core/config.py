from pydantic_settings import BaseSettings, SettingsConfigDict


class MyConfig(BaseSettings):  # type: ignore
    ENV_STATE: str
    DATABASE_URL: str

    # ERP
    ERP_HOST: str
    ERP_USER: str
    ERP_PASSWORD: str
    ERP_API_TOKEN: str

    # AI
    OPENAI_API_KEY: str
    GOOGLE_API_KEY: str

    # Meta WhatsApp Business API
    WHATSAPP_ACCESS_TOKEN: str
    WHATSAPP_PHONE_NUMBER_ID: str
    WHATSAPP_VERIFY_TOKEN: str
    WHATSAPP_BOT_NUMBER: str
    WABA_ID: str

    # Sentry
    SENTRY_DSN: str

    # Others
    MINUTES_BETWEEN_IMAGES: int
    USE_FFMPEG: bool

    # Auth
    ADMIN_API_KEY: str

    # Telegram developer notifications
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_DEV_CHAT_ID: str

    # Server
    SERVER_HOST: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


config = MyConfig()  # type: ignore
""" if config.ENV_STATE == "prod":
    config.ERP_HOST = config.PROD_ERP_HOST
else:
    config.ERP_HOST = config.DEV_ERP_HOST """


if __name__ == "__main__":
    print(config.GOOGLE_API_KEY)

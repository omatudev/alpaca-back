from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Alpaca
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True

    # Database
    database_url: str = "sqlite:///./my_broker.db"

    # Auth
    google_client_id: str = "879038766799-lihogd5k6ed49n9gbv29min1mftfp78h.apps.googleusercontent.com"
    allowed_email: str = "omatu.personal@gmail.com,yesseniaabrilixcanul@gmail.com"
    jwt_secret: str = "change-me-in-production"
    jwt_expiration_days: int = 30


settings = Settings()

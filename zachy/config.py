from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Zachy"
    database_url: str = "sqlite:///./zachy.db"
    debug: bool = False
    garmin_email: str = ""
    garmin_password: str = ""
    # Optional: your own Mapbox public token (pk.…) to enable the Mapbox map styles.
    mapbox_token: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
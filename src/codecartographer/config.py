from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CODECART_")

    database_url: str = "postgresql+psycopg://codecart:codecart@localhost:5432/codecart"


settings = Settings()

from pathlib import Path
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', env_file_encoding='utf-8-sig', extra='ignore')
    database_url: str = 'postgresql+psycopg://knowledge:knowledge@localhost:5432/knowledge'
    model_base_url: str = 'https://api.deepseek.com'
    model_name: str = 'deepseek-flash'
    model_api_key: SecretStr = SecretStr('')
    model_mode: str = 'real'
    embedding_base_url: str = ''
    embedding_model: str = ''
    embedding_api_key: SecretStr = SecretStr('')
    embedding_dim: int = Field(384, ge=1, le=4096)
    embedding_provider: str = 'api'
    reranker_url: str = ''
    reranker_model: str = ''
    reranker_api_key: SecretStr = SecretStr('')
    allowed_origins: str = 'http://localhost:5173,http://localhost:8080,http://127.0.0.1:5173'
    cookie_secure: bool = False
    file_root: Path = ROOT / 'runtime/files'
    seed_password: SecretStr = SecretStr('')
    max_upload_bytes: int = 10 * 1024 * 1024
    max_pdf_pages: int = 100
    tool_timeout: float = 5
    model_timeout: float = 20
    run_timeout: float = 60
    max_tools: int = 6
    max_models: int = 5
    worker_concurrency: int = Field(8, ge=1, le=8)
    simple_document_fast_path: bool = True
    max_context_bytes: int = Field(64000, ge=8000, le=256000)

    @field_validator('file_root')
    @classmethod
    def absolute_file_root(cls, value):
        return value if value.is_absolute() else ROOT / value

    @property
    def origins(self):
        return self.allowed_origins.split(',')


settings = Settings()

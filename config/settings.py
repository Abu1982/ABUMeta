"""配置管理模块"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置类"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # 项目根目录
    BASE_DIR: Path = Path(__file__).resolve().parent.parent

    # LLM API 配置
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4-turbo-preview"

    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_BASE_URL: str = "https://api.anthropic.com"
    ANTHROPIC_MODEL: str = "claude-3-opus-20240229"

    # 数据库配置
    DATABASE_URL: str = "sqlite:///data/memories.db"
    CHROMA_DB_PATH: str = "data/chroma_db"

    # 金库配置
    PENSION_FUND: float = 1000.00
    OPERATIONAL_FUND: float = 500.00
    RISK_FUND: float = 2000.00

    # 日志配置
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "data/logs/agent.log"

    # 爬虫配置
    USER_AGENT: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    REQUEST_TIMEOUT: int = 30
    MAX_RETRIES: int = 3

    # Agent 配置
    DEFAULT_TEMPERATURE: float = 0.7
    DEFAULT_TOP_P: float = 0.9


# 全局配置实例
settings = Settings()

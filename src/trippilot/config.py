"""运行期配置：只从环境变量 / .env 读取，代码里不出现任何明文密钥。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model: str = "text-embedding-v3"
    embedding_dimensions: int = 1024
    rerank_model: str = "gte-rerank"

    amap_maps_api_key: str = ""
    meituan_ht_token: str = ""
    meituan_raw_json: bool = True
    # pg
    postgres_uri: str = ""

    amap_mcp_base_url: str = "https://mcp.amap.com/mcp"
    request_timeout_s: float = 60.0
    # JWT
    secret_key: str = ""
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()

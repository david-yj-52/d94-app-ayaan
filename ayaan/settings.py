from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AYAAN_", extra="ignore")

    query_base_url: str = "http://gateway-cloud-gateway-1:10001/tsh/api/v1/internal/ayaan"
    query_service_token: str = ""
    ecc_base_url: str = "http://gateway-cloud-gateway-1:10001/ecc"
    ecc_token: str = ""
    api_token: str = ""
    jwt_secret_key: str = ""
    workspace_roots: str = "/workspace"
    cli_executables: str = ""
    data_dir: str = "/data"
    profile: str = ""
    eureka_enabled: bool = False
    eureka_url: str = "http://eureka:8761/eureka/"
    eureka_instance_host: str = "d94-app-ayaan"
    eureka_instance_port: int = 8080
    service_name: str = "d94-app-ayaan"
    claude_credentials_path: str = "/home/ayaan/.claude/.credentials.json"
    codex_sessions_dir: str = "/home/ayaan/.codex/sessions"

    @property
    def workspace_root_paths(self) -> tuple[Path, ...]:
        return tuple(Path(v.strip()) for v in self.workspace_roots.split(",") if v.strip())

    @property
    def executable_map(self) -> dict[str, str]:
        result = {}
        for pair in self.cli_executables.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                result[key.strip().lower()] = value.strip()
        return result

    @property
    def fake_enabled(self) -> bool:
        return self.profile == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()

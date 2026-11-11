import os
import shutil
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
    jwt_allowed_subjects: str = ""  # comma list of JWT `sub` (email); empty rejects all JWTs
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
    def allowed_subjects(self) -> set[str]:
        return {v.strip() for v in self.jwt_allowed_subjects.split(",") if v.strip()}

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


DEFAULT_EXE = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "cursor": "agent",
    "git": "git",
}


def resolve_exe(name: str, settings: Settings) -> str | None:
    """Absolute path of a tool CLI (or git): AYAAN_CLI_EXECUTABLES override, else PATH lookup.
    Relative results are rejected: CLIs are only ever run by absolute path."""
    key = name.lower()
    found = settings.executable_map.get(key) or shutil.which(DEFAULT_EXE.get(key, key))
    return found if found and os.path.isabs(found) else None

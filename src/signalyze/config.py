"""Configuration loader. Reads `config/settings.toml` and merges with `.env` overrides."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PathsConfig(BaseModel):
    groups_file: Path = Path("config/groups.txt")
    data_dir: Path = Path("data")
    raw_dir: Path = Path("data/raw")
    db_path: Path = Path("data/db/signalyze.sqlite")
    llm_cache_path: Path = Path("data/cache/llm_cache.sqlite")
    reports_dir: Path = Path("data/reports")


class IngestConfig(BaseModel):
    session_name: str = "session"
    date_from_utc: str = "2026-05-15T00:00:00Z"
    date_to_utc: str = "2026-07-16T00:00:00Z"


class InstrumentConfig(BaseModel):
    default: str = "XAUUSD"
    xauusd_min_price: float = 1500.0
    xauusd_max_price: float = 5000.0


class ClassifyConfig(BaseModel):
    rule_confidence_threshold: float = 0.7
    classifier_version: str = "v0.1"


class ParseConfig(BaseModel):
    parser_version: str = "v0.1"
    llm_escalation_threshold: float = 0.6


class LinkConfig(BaseModel):
    active_window_hours: float = 48.0
    price_match_tolerance: float = 1.0
    llm_tiebreak_epsilon: float = 0.05
    linker_version: str = "v0.1"


class EvaluateConfig(BaseModel):
    win_policy: str = "ANY_TP"
    max_holding_hours: float = 168.0
    default_sl_policy: str = "NONE"
    default_sl_pips: float = 50.0
    evaluator_version: str = "v0.1"


class MarketConfig(BaseModel):
    interval: str = "1min"
    provider: str = "twelvedata"


class EnvOverrides(BaseSettings):
    """Secrets and env-only overrides.

    Loaded by pydantic-settings. Keep small and limited to things that should not
    live in a checked-in TOML file.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_id: str | None = Field(default=None, alias="API_ID")
    api_hash: str | None = Field(default=None, alias="API_HASH")

    llm_provider: str = Field(default="none", alias="SIGNALYZE_LLM_PROVIDER")
    llm_model: str = Field(default="gpt-4o-mini", alias="SIGNALYZE_LLM_MODEL")
    llm_max_usd_per_run: float = Field(default=2.0, alias="SIGNALYZE_LLM_MAX_USD_PER_RUN")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    market_provider: str = Field(default="none", alias="SIGNALYZE_MARKET_PROVIDER")
    twelvedata_api_key: str | None = Field(default=None, alias="TWELVEDATA_API_KEY")


class Settings(BaseModel):
    paths: PathsConfig = PathsConfig()
    ingest: IngestConfig = IngestConfig()
    instrument: InstrumentConfig = InstrumentConfig()
    classify: ClassifyConfig = ClassifyConfig()
    parse: ParseConfig = ParseConfig()
    link: LinkConfig = LinkConfig()
    evaluate: EvaluateConfig = EvaluateConfig()
    market: MarketConfig = MarketConfig()
    env: EnvOverrides = Field(default_factory=EnvOverrides)
    project_root: Path = Path(".")

    def resolve(self, path: Path) -> Path:
        """Resolve a path relative to the project root, leaving absolute paths unchanged."""
        return path if path.is_absolute() else (self.project_root / path).resolve()


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from `config/settings.toml` and `.env`."""
    project_root = _find_project_root(Path.cwd())
    load_dotenv(dotenv_path=project_root / ".env", override=False)

    toml_path = config_path or (project_root / "config" / "settings.toml")
    raw: dict[str, object] = {}
    if toml_path.exists():
        with toml_path.open("rb") as handle:
            raw = tomllib.load(handle)

    settings = Settings.model_validate({**raw, "project_root": project_root})
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor for the running process."""
    return load_settings()


def _find_project_root(start: Path) -> Path:
    """Walk upwards looking for pyproject.toml; default to CWD if not found."""
    for candidate in [start, *start.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return start

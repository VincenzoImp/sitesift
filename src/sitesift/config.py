"""Configuration with precedence CLI > env > file > default.

File: ``./sitesift.toml`` then ``~/.config/sitesift/config.toml``.
Env override: ``SITESIFT_<SECTION>__<KEY>`` (double underscore between section and
key), e.g. ``SITESIFT_IDENTITY__CONTACT=you@example.com``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from . import __version__


class IdentityConfig(BaseModel):
    contact: str = ""  # REQUIRED to run the fetcher (identifying User-Agent)
    project_url: str = "https://github.com/VincenzoImp/sitesift"


class FetchConfig(BaseModel):
    max_concurrency: int = 200
    min_host_delay: float = 1.0
    crawl_delay_clamp: tuple[float, float] = (0.5, 30.0)
    timeout_connect: float = 5.0
    timeout_read: float = 10.0
    timeout_total: float = 20.0
    max_redirects: int = 5
    max_body_bytes: int = 5_242_880  # 5 MiB
    max_decompressed_bytes: int = 20_971_520  # 20 MiB
    retries: int = 3
    allow_ports: tuple[int, ...] = (80, 443)
    respect_robots: bool = True


class ClassifyConfig(BaseModel):
    mode: str = "sync"  # sync = LLM classifies every URL | off = extract facts only
    provider: str = "anthropic"  # anthropic | ollama
    base_url: str = ""  # for ollama/self-hosted; empty = provider default
    model_small: str = "claude-haiku-4-5"
    model_large: str = "claude-sonnet-5"
    max_llm_concurrency: int = 8
    # The small model's verdict is accepted at >= small; otherwise the large model
    # runs and is accepted at >= large. Below both, the verdict goes to a human.
    accept_threshold_small: float = 0.75
    accept_threshold_large: float = 0.60


class TaxonomyConfig(BaseModel):
    id: str = "sitesift-custom-1"  # default small in-repo taxonomy
    path: str = ""  # empty = bundled default


class SecurityConfig(BaseModel):
    allow_private_ips: bool = False


class Settings(BaseSettings):
    """Top-level settings tree."""

    model_config = SettingsConfigDict(
        env_prefix="SITESIFT_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    classify: ClassifyConfig = Field(default_factory=ClassifyConfig)
    taxonomy: TaxonomyConfig = Field(default_factory=TaxonomyConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence (first wins): init (CLI) > env > TOML file > defaults.
        toml_path = _discover_toml(_FORCED_TOML)
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        if toml_path is not None:
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=toml_path))
        return tuple(sources)

    def user_agent(self) -> str:
        contact = self.identity.contact or "no-contact-configured"
        return f"sitesift/{__version__} (+{self.identity.project_url}; contact: {contact})"


def _discover_toml(explicit: str | None = None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for candidate in (
        Path("./sitesift.toml"),
        Path("~/.config/sitesift/config.toml").expanduser(),
    ):
        if candidate.is_file():
            return candidate
    return None


def load_config(path: str | None = None, **overrides: object) -> Settings:
    """Load settings, honoring CLI > env > file > default.

    ``overrides`` are treated as the highest-precedence (CLI) source. ``path``
    forces a specific TOML file (else auto-discovered).
    """
    global _FORCED_TOML
    _FORCED_TOML = path
    return Settings(**overrides)  # type: ignore[arg-type]


# _discover_toml consults this when load_config passes an explicit path.
_FORCED_TOML: str | None = None

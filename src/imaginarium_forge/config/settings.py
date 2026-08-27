"""Application settings loaded from environment / .env (prefix IMF_)."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

OFFICIAL_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-5.6"


class AppSettings(BaseSettings):
    """Runtime settings loaded from ``IMF_*`` variables.

    API credentials deliberately do not belong to this model.  In particular,
    ``OPENAI_API_KEY`` is resolved only by the OpenAI provider at call time (or
    supplied to that provider as an in-memory runtime secret), so settings
    dumps, backups and exports cannot accidentally contain it.
    """

    # A3-R10: the Context Inspector shows the EXACT messages sent to the
    # provider. Hashes are ALWAYS stored; storing the rendered text as well
    # makes historical inspection byte-exact instead of reconstructed.
    #
    # Default ON: this is a local, single-user tool that already keeps the
    # author's drafts and Story Bible on the same disk, so retaining the
    # rendered prompt is consistent with its local-first design. Turn it off
    # for projects preferring stricter data minimisation — the change affects
    # FUTURE runs only and never rewrites historical runs.
    #
    # Privacy note: when enabled, local database backups contain complete
    # private story and prompt text. See docs/CONTEXT_INSPECTION.md.
    store_rendered_generation_messages: bool = True

    # S8.2: a RUNNING generation older than this may be recovered when no
    # matching in-memory Story Studio job is active. One hour is deliberately
    # conservative relative to the normal provider timeout; deployments with
    # longer model calls can raise IMF_STALE_RUNNING_RUN_THRESHOLD_S.
    stale_running_run_threshold_s: float = Field(default=3600.0, gt=0)

    model_config = SettingsConfigDict(
        env_prefix="IMF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: os.pathsep-separated checkpoint scan roots (§7.1), e.g.
    #: IMF_CHECKPOINT_ROOTS=C:\\ComfyUI\\models\\checkpoints
    checkpoint_roots: str = ""

    def checkpoint_root_paths(self) -> tuple["Path", ...]:
        """Parsed scan roots; empty entries dropped."""
        import os
        from pathlib import Path

        return tuple(
            Path(part)
            for part in self.checkpoint_roots.split(os.pathsep)
            if part.strip()
        )

    app_env: Literal["local", "test"] = "local"
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"

    data_dir: Path = Path("data")
    goldenset_dir: Path = Path("goldenset")
    prompt_profiles_dir: Path = Path("config/prompt_profiles")
    database_filename: str = "imaginarium_forge.db"

    @property
    def database_path(self) -> Path:
        """SQLite database file (lives inside data_dir; never committed)."""
        return self.data_dir / self.database_filename

    @property
    def backups_dir(self) -> Path:
        """Backup output directory (inside data_dir; never committed)."""
        return self.data_dir / "backups"

    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout_s: float = 120.0
    ollama_pull_timeout_s: float = Field(default=1800.0, gt=0, le=86_400)
    ollama_keep_alive: str = "5m"
    # "schema": pass the JSON schema through Ollama's `format` field (preferred).
    # "json_mode": format="json" + schema instructions appended to the prompt (fallback
    # if the installed Ollama version rejects schema-valued `format`).
    structured_mode: Literal["schema", "json_mode"] = "schema"
    default_model: str = ""

    # Optional OpenAI Responses API provider.  Ollama/offline remains the
    # application default; these values have no effect until that provider is
    # explicitly selected.  A non-official base URL is intentionally rejected
    # until it can be protected by the same per-send remote privacy gate as the
    # UI's other remote-provider path.
    openai_base_url: str = OFFICIAL_OPENAI_BASE_URL
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_timeout_s: float = Field(default=120.0, gt=0, le=1800)
    openai_max_output_tokens: int = Field(default=8192, ge=1, le=128_000)

    @field_validator("openai_base_url")
    @classmethod
    def _official_openai_endpoint_only(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("OpenAI base URL has an invalid port") from exc
        official = (
            parsed.scheme.lower() == "https"
            and parsed.hostname is not None
            and parsed.hostname.lower() == "api.openai.com"
            and port in {None, 443}
            and parsed.path.rstrip("/") == "/v1"
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
        if not official:
            raise ValueError(
                "only the official OpenAI endpoint is allowed; custom base URLs "
                "require an explicit remote-privacy gate"
            )
        return OFFICIAL_OPENAI_BASE_URL

    @field_validator("openai_model")
    @classmethod
    def _openai_model_not_blank(cls, value: str) -> str:
        model = value.strip()
        if not model:
            raise ValueError("OpenAI model must not be blank")
        return model


def load_settings() -> AppSettings:
    """Load settings from environment (and .env when present)."""
    return AppSettings()

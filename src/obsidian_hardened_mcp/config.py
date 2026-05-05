# SPDX-License-Identifier: Apache-2.0
"""Application configuration.

Loaded once at server startup from CLI args + environment variables.
Holds invariants used across the codebase: vault root, audit/secret paths,
limits, REST endpoint.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_AUDIT_DIR = Path.home() / ".obsidian-hardened-mcp" / "audit"
DEFAULT_SECRET_FILE = Path.home() / ".obsidian-hardened-mcp" / "secret"
DEFAULT_CONFIG_FILE_NAME = ".obsidian-hardened-mcp.yaml"
DEFAULT_MAX_FILE_SIZE_MB = 10
DEFAULT_MAX_BATCH = 500
DEFAULT_REST_URL = "https://127.0.0.1:27124"


class AppConfig(BaseModel):
    """Runtime configuration."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    vault_root: Path = Field(description="Absolute path to the Obsidian vault root.")
    audit_dir: Path = DEFAULT_AUDIT_DIR
    secret_file: Path = DEFAULT_SECRET_FILE
    config_file_name: str = DEFAULT_CONFIG_FILE_NAME
    max_file_size_mb: int = DEFAULT_MAX_FILE_SIZE_MB
    max_batch: int = DEFAULT_MAX_BATCH
    rest_url: str = DEFAULT_REST_URL
    rest_token: str | None = None

    @field_validator("vault_root")
    @classmethod
    def _vault_root_must_exist(cls, value: Path) -> Path:
        resolved = value.expanduser().resolve(strict=False)
        if not resolved.exists():
            raise ValueError(f"vault root does not exist: {resolved}")
        if not resolved.is_dir():
            raise ValueError(f"vault root is not a directory: {resolved}")
        return resolved

    @field_validator("max_file_size_mb")
    @classmethod
    def _max_file_size_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_file_size_mb must be positive")
        return value

    @field_validator("max_batch")
    @classmethod
    def _max_batch_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_batch must be positive")
        return value

    @field_validator("rest_url")
    @classmethod
    def _rest_url_must_be_loopback(cls, value: str) -> str:
        """Refuse non-loopback REST URLs.

        v0.1 ships with `verify=False` on the httpx client because the
        Obsidian Local REST API plugin uses a self-signed cert for
        `127.0.0.1`. That posture is only safe on loopback — pointing
        the client at a remote host would happily send the bearer
        token in cleartext to whoever answered. Refuse the
        configuration up-front.

        v0.2 followup (M7-03) can relax this with a user-provided CA
        bundle.
        """
        from urllib.parse import urlparse

        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                f"rest_url must point at loopback (127.0.0.1, localhost, ::1); "
                f"got {host!r}. Non-loopback URLs are refused in v0.1 because "
                f"verify_tls is disabled by default — see M7-03 followup."
            )
        return value

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @classmethod
    def from_env(cls, vault_root: Path | str, **overrides: object) -> AppConfig:
        """Build a config from a vault path plus optional `OBSIDIAN_*` env
        vars. Caller-supplied `overrides` (typically CLI flags) take
        precedence over env values."""
        kwargs: dict[str, object] = {"vault_root": Path(vault_root)}
        if (token := os.getenv("OBSIDIAN_REST_TOKEN")) is not None:
            kwargs["rest_token"] = token
        if (url := os.getenv("OBSIDIAN_REST_URL")) is not None:
            kwargs["rest_url"] = url
        if (audit := os.getenv("OBSIDIAN_AUDIT_DIR")) is not None:
            # Allows CI runners (or paranoid users) to relocate audit logs
            # outside the default ~/.obsidian-hardened-mcp/audit/. Useful when
            # publishing test artefacts that would otherwise leak $HOME.
            kwargs["audit_dir"] = Path(audit).expanduser()
        kwargs.update(overrides)
        return cls(**kwargs)  # type: ignore[arg-type]

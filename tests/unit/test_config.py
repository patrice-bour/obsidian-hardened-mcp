"""Unit tests for AppConfig."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from obsidian_power_mcp.config import AppConfig


class TestVaultRootValidation:
    def test_existing_dir_is_accepted_and_resolved(self, tmp_vault: Path) -> None:
        cfg = AppConfig(vault_root=tmp_vault)
        assert cfg.vault_root == tmp_vault.resolve()

    def test_missing_root_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError):
            AppConfig(vault_root=tmp_path / "ghost")

    def test_file_root_is_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "not_a_dir"
        f.write_text("hi")
        with pytest.raises(ValidationError):
            AppConfig(vault_root=f)


class TestSizeLimitsValidation:
    def test_zero_max_file_size_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(ValidationError):
            AppConfig(vault_root=tmp_vault, max_file_size_mb=0)

    def test_zero_max_batch_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(ValidationError):
            AppConfig(vault_root=tmp_vault, max_batch=0)

    def test_max_file_size_bytes_property(self, tmp_vault: Path) -> None:
        cfg = AppConfig(vault_root=tmp_vault, max_file_size_mb=5)
        assert cfg.max_file_size_bytes == 5 * 1024 * 1024


class TestFromEnv:
    def test_env_vars_propagate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_vault: Path
    ) -> None:
        monkeypatch.setenv("OBSIDIAN_REST_TOKEN", "token-xyz")
        monkeypatch.setenv("OBSIDIAN_REST_URL", "https://10.0.0.1:27124")
        cfg = AppConfig.from_env(tmp_vault)
        assert cfg.rest_token == "token-xyz"
        assert cfg.rest_url == "https://10.0.0.1:27124"

    def test_env_vars_absent_uses_defaults(
        self, monkeypatch: pytest.MonkeyPatch, tmp_vault: Path
    ) -> None:
        monkeypatch.delenv("OBSIDIAN_REST_TOKEN", raising=False)
        monkeypatch.delenv("OBSIDIAN_REST_URL", raising=False)
        cfg = AppConfig.from_env(tmp_vault)
        assert cfg.rest_token is None
        assert cfg.rest_url == "https://127.0.0.1:27124"

"""Unit tests for AppConfig."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from obsidian_hardened_mcp.config import AppConfig


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

    def test_zero_max_batch_bytes_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(ValidationError):
            AppConfig(vault_root=tmp_vault, max_batch_bytes=0)

    def test_negative_max_batch_bytes_is_rejected(self, tmp_vault: Path) -> None:
        with pytest.raises(ValidationError):
            AppConfig(vault_root=tmp_vault, max_batch_bytes=-1)

    def test_max_batch_bytes_default_is_10mb(self, tmp_vault: Path) -> None:
        cfg = AppConfig(vault_root=tmp_vault)
        assert cfg.max_batch_bytes == 10 * 1024 * 1024

    def test_max_batch_bytes_custom(self, tmp_vault: Path) -> None:
        cfg = AppConfig(vault_root=tmp_vault, max_batch_bytes=5 * 1024 * 1024)
        assert cfg.max_batch_bytes == 5 * 1024 * 1024


class TestFromEnv:
    def test_env_vars_propagate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_vault: Path
    ) -> None:
        monkeypatch.setenv("OBSIDIAN_REST_TOKEN", "token-xyz")
        # Use a loopback host — non-loopback is refused (M7.5).
        monkeypatch.setenv("OBSIDIAN_REST_URL", "https://localhost:27124")
        cfg = AppConfig.from_env(tmp_vault)
        assert cfg.rest_token == "token-xyz"
        assert cfg.rest_url == "https://localhost:27124"

    def test_env_vars_absent_uses_defaults(
        self, monkeypatch: pytest.MonkeyPatch, tmp_vault: Path
    ) -> None:
        monkeypatch.delenv("OBSIDIAN_REST_TOKEN", raising=False)
        monkeypatch.delenv("OBSIDIAN_REST_URL", raising=False)
        monkeypatch.delenv("OBSIDIAN_AUDIT_DIR", raising=False)
        cfg = AppConfig.from_env(tmp_vault)
        assert cfg.rest_token is None
        assert cfg.rest_url == "https://127.0.0.1:27124"
        assert cfg.audit_dir == Path.home() / ".obsidian-hardened-mcp" / "audit"

    def test_audit_dir_env_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_vault: Path, tmp_path: Path
    ) -> None:
        sandbox = tmp_path / "audit-sandbox"
        monkeypatch.setenv("OBSIDIAN_AUDIT_DIR", str(sandbox))
        cfg = AppConfig.from_env(tmp_vault)
        assert cfg.audit_dir == sandbox

    def test_audit_dir_env_expands_tilde(
        self, monkeypatch: pytest.MonkeyPatch, tmp_vault: Path
    ) -> None:
        monkeypatch.setenv("OBSIDIAN_AUDIT_DIR", "~/custom-audit")
        cfg = AppConfig.from_env(tmp_vault)
        assert cfg.audit_dir == Path.home() / "custom-audit"

    def test_overrides_take_precedence_over_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_vault: Path
    ) -> None:
        monkeypatch.setenv("OBSIDIAN_REST_TOKEN", "from-env")
        cfg = AppConfig.from_env(tmp_vault, rest_token="from-cli")
        assert cfg.rest_token == "from-cli"

    def test_require_elicitation_env_true(
        self, monkeypatch: pytest.MonkeyPatch, tmp_vault: Path
    ) -> None:
        for truthy in ("true", "True", "TRUE", "1", "yes", "YES"):
            monkeypatch.setenv("OBSIDIAN_REQUIRE_ELICITATION", truthy)
            cfg = AppConfig.from_env(tmp_vault)
            assert cfg.require_elicitation is True, f"failed for {truthy!r}"

    def test_require_elicitation_env_false(
        self, monkeypatch: pytest.MonkeyPatch, tmp_vault: Path
    ) -> None:
        for falsy in ("false", "0", "no", "", "anything-else"):
            monkeypatch.setenv("OBSIDIAN_REQUIRE_ELICITATION", falsy)
            cfg = AppConfig.from_env(tmp_vault)
            assert cfg.require_elicitation is False, f"failed for {falsy!r}"

    def test_require_elicitation_env_absent_uses_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_vault: Path
    ) -> None:
        monkeypatch.delenv("OBSIDIAN_REQUIRE_ELICITATION", raising=False)
        cfg = AppConfig.from_env(tmp_vault)
        assert cfg.require_elicitation is False  # v0.3.1 default


class TestRestUrlLoopbackOnly:
    """M7.5 — rest_url must point at loopback. Otherwise the
    `verify=False` posture would expose the bearer token to whoever
    answers a remote request."""

    def test_localhost_accepted(self, tmp_vault: Path) -> None:
        cfg = AppConfig(
            vault_root=tmp_vault,
            rest_url="https://localhost:27124",
        )
        assert cfg.rest_url == "https://localhost:27124"

    def test_ipv4_loopback_accepted(self, tmp_vault: Path) -> None:
        cfg = AppConfig(
            vault_root=tmp_vault,
            rest_url="https://127.0.0.1:27124",
        )
        assert cfg.rest_url == "https://127.0.0.1:27124"

    def test_ipv6_loopback_accepted(self, tmp_vault: Path) -> None:
        cfg = AppConfig(
            vault_root=tmp_vault,
            rest_url="https://[::1]:27124",
        )
        assert cfg.rest_url == "https://[::1]:27124"

    def test_remote_host_refused(self, tmp_vault: Path) -> None:
        with pytest.raises(ValueError, match="loopback"):
            AppConfig(
                vault_root=tmp_vault,
                rest_url="https://attacker.example.com:27124",
            )

    def test_private_ip_refused(self, tmp_vault: Path) -> None:
        with pytest.raises(ValueError, match="loopback"):
            AppConfig(
                vault_root=tmp_vault,
                rest_url="https://10.0.0.1:27124",
            )

    def test_zero_bind_refused(self, tmp_vault: Path) -> None:
        with pytest.raises(ValueError, match="loopback"):
            AppConfig(
                vault_root=tmp_vault,
                rest_url="http://0.0.0.0:27124",
            )

    def test_require_elicitation_default_false(self, tmp_vault: Path) -> None:
        cfg = AppConfig(vault_root=tmp_vault)
        assert cfg.require_elicitation is False

    def test_require_elicitation_optin_true(self, tmp_vault: Path) -> None:
        cfg = AppConfig(vault_root=tmp_vault, require_elicitation=True)
        assert cfg.require_elicitation is True

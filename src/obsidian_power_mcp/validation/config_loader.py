"""Loader for `.obsidian-power-mcp.yaml` (vault-root configuration).

The config file is OPTIONAL — when absent, an empty `HookRegistry` is
returned and writes proceed without any pluggable validation. The presence
of the file activates user-declared hooks.

Schema files referenced by `json_schema` hooks live in the vault and are
resolved relative to the vault root. Path traversal in schema paths is
rejected — schemas may not point outside the vault.

Format (v0.1):

```yaml
hooks:
  - iso_date                                # no-arg form
  - iso_date:
      fields: [date, due-date]              # arg form (positional or kwargs)
  - reserved_tags:
      forbidden: [migration-cc]
      forbidden_fields: [source-vault]
  - json_schema:
      schemas:
        offre-emploi: _schemas/offre-emploi.json   # relative to vault root
```

Unknown hook names, unknown hook kwargs, missing schema files, schemas
that escape the vault, and malformed YAML all raise `ConfigError` at
load time so problems show up at boot, not on the first write.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import jsonschema
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from obsidian_power_mcp.frontmatter.yaml_safety import enforce_default_tags_only
from obsidian_power_mcp.validation.builtin_hooks import (
    IsoDateHook,
    JsonSchemaHook,
    ReservedTagsHook,
)
from obsidian_power_mcp.validation.hooks import HookRegistry, ValidationHook

CONFIG_FILE_NAME = ".obsidian-power-mcp.yaml"

# `type[X]` is invariant; the registry just needs callables that produce a
# `ValidationHook`. Using `Any` here keeps mypy happy without losing type
# safety at the call site (`_validate_kwargs` checks the signature, and the
# constructed instance is asserted against the Protocol below).
_BUILTIN_HOOKS: dict[str, Any] = {
    "iso_date": IsoDateHook,
    "reserved_tags": ReservedTagsHook,
    "json_schema": JsonSchemaHook,
}


class ConfigError(Exception):
    """Raised when the validation config cannot be loaded or is malformed."""


def load_validation_config(vault_root: Path) -> HookRegistry:
    """Load `.obsidian-power-mcp.yaml` (if present) and return a
    `HookRegistry` ready to plug into the write path.

    Raises `ConfigError` on any malformed input — never returns a half-built
    registry.
    """
    vault_root = vault_root.resolve(strict=True)
    config_path = vault_root / CONFIG_FILE_NAME
    if not config_path.exists():
        return HookRegistry([])

    try:
        yaml = YAML(typ="rt")
        yaml.preserve_quotes = True
        with config_path.open("r", encoding="utf-8") as fp:
            raw = yaml.load(fp)
    except YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc

    # Same custom-tag policy as note frontmatter — invariant #5 of the
    # project applies project-wide, not just to vault notes.
    enforce_default_tags_only(raw, error_class=ConfigError)

    if raw is None:
        return HookRegistry([])
    if not isinstance(raw, dict):
        raise ConfigError(
            f"top level of {CONFIG_FILE_NAME} must be a mapping, got "
            f"{type(raw).__name__}"
        )

    hook_specs = raw.get("hooks") or []
    if not isinstance(hook_specs, list):
        raise ConfigError("`hooks` must be a list")

    hooks: list[ValidationHook] = []
    for spec in hook_specs:
        hooks.append(_build_hook(spec, vault_root=vault_root))
    return HookRegistry(hooks)


def _build_hook(spec: object, *, vault_root: Path) -> ValidationHook:
    if isinstance(spec, str):
        name, kwargs = spec, {}
    elif isinstance(spec, dict):
        if len(spec) != 1:
            raise ConfigError(
                f"hook entry must have exactly one key (the hook name), "
                f"got {list(spec.keys())}"
            )
        name = next(iter(spec))
        body = spec[name]
        kwargs = _coerce_kwargs(name, body)
    else:
        raise ConfigError(
            f"hook entry must be a string or single-key mapping, got "
            f"{type(spec).__name__}"
        )

    factory = _BUILTIN_HOOKS.get(name)
    if factory is None:
        raise ConfigError(
            f"unknown hook {name!r}; available: {sorted(_BUILTIN_HOOKS)}"
        )
    if name == "json_schema":
        kwargs = {"schemas": _load_schemas(kwargs.get("schemas", {}), vault_root)}

    _validate_kwargs(name, factory, kwargs)
    try:
        instance = factory(**kwargs)
    except (TypeError, ValueError, jsonschema.SchemaError) as exc:
        raise ConfigError(
            f"failed to construct hook {name!r}: {exc}"
        ) from exc
    if not isinstance(instance, ValidationHook):  # pragma: no cover - defensive
        raise ConfigError(
            f"hook {name!r} factory returned an object that does not "
            f"implement ValidationHook"
        )
    return instance


def _coerce_kwargs(hook_name: str, body: object) -> dict[str, Any]:
    if body is None:
        return {}
    if isinstance(body, dict):
        return {str(k): _plain(v) for k, v in body.items()}
    raise ConfigError(
        f"hook {hook_name!r} arguments must be a mapping, got {type(body).__name__}"
    )


def _plain(value: Any) -> Any:
    """Recursively coerce ruamel containers to plain dict/list/scalars."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _validate_kwargs(
    hook_name: str, factory: Any, kwargs: dict[str, Any]
) -> None:
    sig = inspect.signature(factory)
    accepted = set(sig.parameters.keys()) - {"self"}
    extra = set(kwargs) - accepted
    if extra:
        raise ConfigError(
            f"hook {hook_name!r} got unknown argument(s) {sorted(extra)}; "
            f"accepted: {sorted(accepted)}"
        )


def _load_schemas(
    raw_map: object, vault_root: Path
) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_map, dict):
        raise ConfigError(
            f"`schemas` must be a mapping, got {type(raw_map).__name__}"
        )
    schemas: dict[str, dict[str, Any]] = {}
    for type_name, rel_path in raw_map.items():
        if not isinstance(rel_path, str):
            raise ConfigError(
                f"schema path for {type_name!r} must be a string"
            )
        absolute = (vault_root / rel_path).resolve(strict=False)
        try:
            absolute.relative_to(vault_root)
        except ValueError as exc:
            raise ConfigError(
                f"schema path for {type_name!r} resolves outside vault: "
                f"{rel_path!r}"
            ) from exc
        if not absolute.exists():
            raise ConfigError(
                f"schema file for {type_name!r} not found: {rel_path}"
            )
        try:
            schemas[str(type_name)] = json.loads(absolute.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"schema file {rel_path!r} is not valid JSON: {exc}"
            ) from exc
    return schemas

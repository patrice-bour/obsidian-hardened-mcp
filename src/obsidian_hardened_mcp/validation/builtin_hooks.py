# SPDX-License-Identifier: Apache-2.0
"""Built-in validation hooks.

Each hook here implements the `ValidationHook` Protocol from
`validation.hooks`. They are configured in `.obsidian-hardened-mcp.yaml`:

```yaml
hooks:
  - iso_date
  - reserved_tags:
      forbidden: [migration-cc, migration/legacy]
      forbidden_fields: [source-vault]
  - json_schema:
      schemas:
        offre-emploi: schemas/offre-emploi.json
```

Hook descriptions:

- `iso_date` — fields named `date` (or any custom set) must be ISO-8601
  strings. Catches the most common pbkm/Dataview corruption.
- `reserved_tags` — refuse specific tags or specific top-level fields.
  Used to keep migration markers (e.g. `migration-cc`, `source-vault:`)
  out of newly-created notes.
- `json_schema` — when frontmatter has a `type:` matching a registered
  schema, validate the whole frontmatter against that JSON Schema.
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Iterable
from typing import Any

import jsonschema

from obsidian_hardened_mcp.validation.hooks import HookContext, HookResult


class CyclicRefError(ValueError):
    """Raised when a JSON Schema contains `$ref` cycles that would cause
    `iter_errors` to recurse infinitely. Detected at hook construction time
    so the error surfaces at server boot, not on the first write."""

# ---------------------------------------------------------------------------
# IsoDateHook
# ---------------------------------------------------------------------------


class IsoDateHook:
    """Reject frontmatter where any configured date field is not ISO-8601.

    Default checks the `date` key. Pass `fields=("date", "due-date", ...)`
    to extend the set.
    """

    name = "iso_date"
    phase = "pre_write"

    def __init__(self, fields: Iterable[str] = ("date",)) -> None:
        self._fields = tuple(fields)

    def validate(self, ctx: HookContext) -> HookResult:
        fm = ctx.new_frontmatter
        if fm is None:
            return HookResult.accept()
        for field in self._fields:
            if field not in fm:
                continue
            value = fm[field]
            if not isinstance(value, str):
                return HookResult.reject(
                    f"{field!r} must be an ISO-8601 string, got "
                    f"{type(value).__name__}"
                )
            if not _is_iso_date(value):
                return HookResult.reject(
                    f"{field!r} = {value!r} is not a valid ISO-8601 date / datetime"
                )
        return HookResult.accept()


def _is_iso_date(value: str) -> bool:
    """Accept either `YYYY-MM-DD` or full ISO-8601 datetime (with tz)."""
    if not value:
        return False
    # Try date-only first (most common in vault frontmatter).
    try:
        dt.date.fromisoformat(value)
        return True
    except ValueError:
        pass
    # Try full datetime — `fromisoformat` accepts `Z` suffix in Python 3.11+.
    try:
        dt.datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# ReservedTagsHook
# ---------------------------------------------------------------------------


class ReservedTagsHook:
    """Refuse forbidden tags or forbidden top-level fields.

    `forbidden`: tag values that must NOT appear in `tags:`.
    `forbidden_fields`: top-level frontmatter keys that must NOT exist.

    Used to keep migration markers, audit injections, etc. out of
    newly-authored notes. Both lists default empty (no-op hook).
    """

    name = "reserved_tags"
    phase = "pre_write"

    def __init__(
        self,
        forbidden: Iterable[str] = (),
        forbidden_fields: Iterable[str] = (),
    ) -> None:
        self._forbidden_tags = frozenset(forbidden)
        self._forbidden_fields = frozenset(forbidden_fields)

    def validate(self, ctx: HookContext) -> HookResult:
        fm = ctx.new_frontmatter
        if fm is None:
            return HookResult.accept()
        for field_name in self._forbidden_fields:
            if field_name in fm:
                return HookResult.reject(
                    f"frontmatter must not contain reserved field "
                    f"{field_name!r}"
                )
        tags = fm.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                if tag in self._forbidden_tags:
                    return HookResult.reject(
                        f"tag {tag!r} is reserved and cannot be set on a note"
                    )
        return HookResult.accept()


# ---------------------------------------------------------------------------
# JsonSchemaHook
# ---------------------------------------------------------------------------


class JsonSchemaHook:
    """Validate frontmatter against a JSON Schema selected by `type:`.

    `schemas` maps a `type` value to a parsed JSON Schema (as a dict).
    Frontmatters without a `type` key, or with a `type` not in the map,
    are accepted (it is not this hook's job to enforce a type whitelist —
    use `reserved_tags` or a custom hook for that).
    """

    name = "json_schema"
    phase = "pre_write"

    def __init__(self, schemas: dict[str, dict[str, Any]]) -> None:
        # Validate each schema once at construction so we fail loudly on
        # malformed config rather than at first hook run.
        for type_name, schema in schemas.items():
            jsonschema.Draft202012Validator.check_schema(schema)
            _check_no_cyclic_refs(type_name, schema)
        self._validators: dict[str, jsonschema.Draft202012Validator] = {
            type_name: jsonschema.Draft202012Validator(schema)
            for type_name, schema in schemas.items()
        }

    def validate(self, ctx: HookContext) -> HookResult:
        fm = ctx.new_frontmatter
        if fm is None:
            return HookResult.accept()
        type_name = fm.get("type")
        if not isinstance(type_name, str):
            return HookResult.accept()
        validator = self._validators.get(type_name)
        if validator is None:
            return HookResult.accept()

        errors = sorted(validator.iter_errors(fm), key=lambda e: list(e.path))
        if not errors:
            return HookResult.accept()
        first = errors[0]
        location = ".".join(str(p) for p in first.absolute_path) or "<root>"
        return HookResult.reject(
            f"schema {type_name!r} violation at {location}: {first.message}"
        )


# Sample inputs used to probe a schema for cyclic-`$ref` recursion bombs.
# A real cyclic schema infinite-loops on ANY input, so a small set is enough.
_PROBE_INPUTS: tuple[Any, ...] = (
    None,
    True,
    0,
    "",
    [],
    [1],
    {},
    {"x": 1},
)


def _check_no_cyclic_refs(type_name: str, schema: dict[str, Any]) -> None:
    """Probe a schema with sample inputs under a lowered recursion limit.

    `Draft202012Validator.check_schema` does NOT detect mutually-recursive
    `$ref` constructs (e.g. `A → B → A`). Such schemas accept construction
    but explode with `RecursionError` on the first real `iter_errors` call,
    locking every subsequent write with a hook crash.

    We pre-flight a small set of probe inputs under a bounded recursion
    limit. If the validator recurses infinitely, we raise `CyclicRefError`
    and the server refuses to boot — fail loud, not at first write.

    `sys.setrecursionlimit` is process-global and thread-unsafe; we restore
    it in `finally`. The probe is synchronous and short, so no other code
    runs at the lowered limit.
    """
    validator = jsonschema.Draft202012Validator(schema)
    probe_limit = 300
    old_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(min(old_limit, probe_limit))
        for probe in _PROBE_INPUTS:
            try:
                list(validator.iter_errors(probe))
            except RecursionError as exc:
                raise CyclicRefError(
                    f"schema {type_name!r} has cyclic $refs that would "
                    f"recurse infinitely at validation time: {exc}"
                ) from exc
    finally:
        sys.setrecursionlimit(old_limit)

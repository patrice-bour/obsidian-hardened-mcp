# Config reference — `.obsidian-power-mcp.yaml`

The validation config file lives at the **root of the vault**, named
`.obsidian-power-mcp.yaml`. The file is **optional**: if it is absent, no
hooks run and writes proceed with the baseline safety only (path sandbox,
atomic writer, YAML safety, write-time type whitelist).

## Format (v0.1)

```yaml
hooks:
  - <hook_name>                  # no-args form
  - <hook_name>:                 # args form
      <kwarg>: <value>
      ...
```

The list is ordered: hooks run sequentially in declaration order, the
first `reject` short-circuits and the corresponding tool call returns a
`ToolResult.failure(VALIDATION_FAILED, ...)`. `warn` outcomes accumulate
into a report but do not block.

A crashing hook is treated as a rejection — the registry never opens the
door because of an unexpected exception.

## Built-in hooks

### `iso_date`

Reject frontmatter where any configured date field is not an ISO-8601
date or datetime string.

```yaml
- iso_date                                # default: checks `date:` only
- iso_date:
    fields: [date, due-date, expires]     # check all three
```

Accepted shapes:
- `2026-05-04` (date)
- `2026-05-04T10:30:00` (datetime, no tz)
- `2026-05-04T10:30:00Z` (datetime, UTC)
- `2026-05-04T10:30:00+02:00` (datetime, offset)

Rejected: `2026/05/04`, `04-05-2026`, `tomorrow`, `2026-13-01`, `20260504`.

### `reserved_tags`

Refuse forbidden tags or forbidden top-level fields. Used to keep
migration markers, audit injections, and other reserved values out of
newly-authored notes.

```yaml
- reserved_tags:
    forbidden: [migration-cc, migration/pbr]
    forbidden_fields: [source-vault]
```

`forbidden`: tag values that must NOT appear in `tags:` (exact-match;
hierarchical tags compare as full strings, so `migration` and
`migration/pbr` are distinct).

`forbidden_fields`: top-level frontmatter keys that must NOT exist.

### `json_schema`

Validate frontmatter against a JSON Schema selected by `type:`. Schemas
are stored in the vault and referenced by relative path.

```yaml
- json_schema:
    schemas:
      offre-emploi: _schemas/offre-emploi.json
      candidature: _schemas/candidature.json
```

Schemas are validated as Draft 2020-12 at config load time — invalid
schemas surface as `ConfigError` at server boot, not at first write.
Path traversal in schema paths (`../escape.json`) is rejected.

When a frontmatter has no `type:` field, or the `type:` value is not
in the `schemas` map, this hook accepts the operation (it is not the
schema's job to enforce a type whitelist — use `reserved_tags` or a
custom hook for that).

Example schema (`_schemas/offre-emploi.json`):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["type", "date", "recruteur"],
  "properties": {
    "type": {"const": "offre-emploi"},
    "date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
    "recruteur": {"type": "string"},
    "poste": {"type": "string"}
  },
  "additionalProperties": true
}
```

## Loading semantics

- The file is **read once** at server startup. Restart the server to
  reload after editing the config.
- Errors at load time (unknown hook name, unknown kwarg, invalid YAML,
  missing schema file, schema escaping the vault, malformed JSON
  Schema) raise `ConfigError` and abort startup — fail loud, not at
  first write.
- An empty config (or a config with no `hooks:` section) returns an
  empty registry — equivalent to "no validation".

## Operational notes

- The config file lives in the **forbidden write zone**: tools cannot
  modify `.obsidian-power-mcp.yaml` themselves. Edit it manually with
  your text editor, then restart the server.
- Hooks see the **post-write** state (frontmatter + body), not the
  inputs that produced it. This means a `set_frontmatter_field` and a
  `merge_frontmatter` reaching the same final state run the same
  validation.
- Hooks do NOT have access to the **previous** state of the file. If a
  hook needs to compare before/after (e.g. forbid certain transitions),
  it must read the file itself — but that is outside the scope of the
  v0.1 Protocol.

## Custom hooks (v0.2+)

The `ValidationHook` Protocol is public; user-defined classes can be
registered via Python entry-points in a future release. v0.1 only
supports the three built-in names listed above.

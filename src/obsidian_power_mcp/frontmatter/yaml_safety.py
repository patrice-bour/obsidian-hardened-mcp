"""Shared YAML safety helpers.

`enforce_default_tags_only` walks a ruamel-parsed structure and refuses any
explicit YAML tag that is not on the YAML 1.2 default whitelist. Used
both for note frontmatter (`frontmatter.parser`) and for the project
config file `.obsidian-power-mcp.yaml` (`validation.config_loader`).

ruamel's round-trip loader does NOT execute Python object tags (it just
drops the tag and returns a plain list/dict-like value), but the tag is
preserved on the round-trip metadata. If we wrote that back unchanged, a
subsequent reader running PyYAML in unsafe mode WOULD execute the
callable. We therefore refuse any tag outside the YAML 1.2 default set
at PARSE time, project-wide.
"""

from __future__ import annotations

SAFE_TAG_VALUES: frozenset[str] = frozenset(
    {
        "tag:yaml.org,2002:str",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:null",
        "tag:yaml.org,2002:seq",
        "tag:yaml.org,2002:map",
        "tag:yaml.org,2002:timestamp",
        "tag:yaml.org,2002:binary",
        "tag:yaml.org,2002:omap",
        "tag:yaml.org,2002:set",
    }
)


def enforce_default_tags_only(
    node: object, *, error_class: type[Exception]
) -> None:
    """Walk `node`, raise `error_class(...)` on the first non-default tag.

    Default tags are the YAML 1.2 official ones (`tag:yaml.org,2002:*`)
    listed in `SAFE_TAG_VALUES`. Any `!Custom`, `!!python/object/...`,
    arbitrary `!Foo`, etc. is rejected.

    Caller passes the exception class so the same primitive can raise a
    domain-specific error (`UnsafeYamlError` for note frontmatter,
    `ConfigError` for the project config file).
    """
    tag = getattr(node, "tag", None)
    if tag is not None:
        tag_value = getattr(tag, "value", None)
        if tag_value is not None and tag_value not in SAFE_TAG_VALUES:
            raise error_class(f"unsafe YAML tag: {tag_value}")
    if isinstance(node, dict):
        for value in node.values():
            enforce_default_tags_only(value, error_class=error_class)
    elif isinstance(node, list):
        for item in node:
            enforce_default_tags_only(item, error_class=error_class)

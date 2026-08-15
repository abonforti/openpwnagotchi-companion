#!/usr/bin/env python3
"""Validates docs/schemas, the authoritative definition of the wire format.

docs/schemas is the single source of truth for every message (SPEC.md D15): the
plugin is tested against it, the frontend's TypeScript types are generated from
it, and docs/PROTOCOL.md deliberately does not restate it. That only holds if the
schemas themselves are correct, which is what this checks.

The invariants, and why each one matters:

  1. Every file is a valid JSON Schema draft 2020-12. A malformed schema silently
     validates nothing, so a broken one is worse than a missing one.
  2. A message schema's `type` const equals its filename. The dispatcher keys on
     the filename; a mismatch means a message nobody can route.
  3. Every message carries the framing its direction requires - incoming is flat,
     outgoing wraps its payload under `data` with a `timestamp`.
  4. `additionalProperties: false` throughout, so an unexpected key is an error
     rather than something silently ignored on one side of the link.
  5. Every `$ref` resolves. A typo in a ref is otherwise invisible until runtime.
  6. Every definition in common.json is actually referenced, so dead shapes do
     not accumulate and mislead a reader into implementing them.

Usage: python .github/check_schemas.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:
    print("check_schemas: jsonschema is not installed (pip install jsonschema)", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent / "docs" / "schemas"

problems: list[str] = []


def fail(where: str, message: str) -> None:
    problems.append(f"{where}: {message}")


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        fail(path.name, f"invalid JSON: {err}")
        return None


def walk(node, path: str):
    """Yields (json_pointer, subschema) for every dict in the tree."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from walk(value, f"{path}/{i}")


common_path = ROOT / "common.json"
common = load(common_path)
if common is None:
    print("\n".join(problems), file=sys.stderr)
    sys.exit(1)

defs = set(common.get("$defs", {}))
referenced: set[str] = set()

message_files = sorted(
    [p for p in (ROOT / "incoming").glob("*.json")]
    + [p for p in (ROOT / "outgoing").glob("*.json")]
)
all_files = [common_path] + message_files

REF = re.compile(r"^(?:common\.json)?#/\$defs/(\w+)$")

for path in all_files:
    schema = load(path)
    if schema is None:
        continue
    rel = f"{path.parent.name}/{path.name}" if path.parent.name != "schemas" else path.name

    # 1. valid draft 2020-12
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as err:
        fail(rel, f"not a valid draft 2020-12 schema: {err}")
        continue

    # 5. refs resolve, and collect usage for check 6
    for pointer, node in walk(schema, ""):
        ref = node.get("$ref") if isinstance(node, dict) else None
        if ref is None:
            continue
        match = REF.match(ref)
        if not match:
            fail(rel, f"unsupported $ref {ref!r} at {pointer}")
            continue
        name = match.group(1)
        referenced.add(name)
        if name not in defs:
            fail(rel, f"$ref points at unknown definition {name!r} at {pointer}")

    if path == common_path:
        continue

    direction = path.parent.name
    name = path.stem
    props = schema.get("properties", {})

    # 2. type const matches the filename
    const = props.get("type", {}).get("const")
    if const != name:
        fail(rel, f"type const is {const!r}, expected {name!r}")

    # 3. framing per direction
    required = set(schema.get("required", []))
    if "type" not in required:
        fail(rel, "'type' must be required")
    if direction == "outgoing":
        if "data" not in props:
            fail(rel, "outgoing messages must carry their payload under 'data'")
        if not {"data", "timestamp"} <= required:
            fail(rel, "outgoing messages must require 'data' and 'timestamp'")
    elif "data" in props:
        fail(rel, "incoming messages are flat: payload fields sit beside 'type', not under 'data'")

    # 4. no silently ignored keys
    for pointer, node in walk(schema, ""):
        if node.get("type") == "object" and "additionalProperties" not in node:
            fail(rel, f"object at {pointer or '/'} does not set additionalProperties")

# 6. no dead definitions
for unused in sorted(defs - referenced):
    fail("common.json", f"definition {unused!r} is never referenced")

if problems:
    for line in problems:
        print(line, file=sys.stderr)
    print(f"\n{len(problems)} problem(s) in docs/schemas.", file=sys.stderr)
    sys.exit(1)

print(
    f"schemas: {len(all_files)} file(s) valid, "
    f"{len(defs)} shared definition(s), all referenced, all $refs resolve."
)

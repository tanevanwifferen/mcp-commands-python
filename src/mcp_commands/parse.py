"""Turning a command line (plus an optional args object) into a command and its values."""

from __future__ import annotations

import difflib
import json
import shlex
from typing import Any

from mcp_commands.errors import CommandError, UsageError
from mcp_commands.tree import Command, Group, Param, _schema_types


def split_path(path: str) -> list[str]:
    """Split a help path such as "issue create", "issue/create" or "issue > create"."""
    return [t for t in path.replace("/", " ").replace(">", " ").split() if t]


def tokenize(line: str) -> list[str]:
    try:
        return shlex.split(line)
    except ValueError as e:
        raise CommandError(f"cannot parse command line: {e}") from None


def walk(root: Group, tokens: list[str]) -> tuple[Group | Command, list[str], list[str]]:
    """Follow tokens down the tree as far as they name groups and commands.

    Returns the node reached, the canonical path to it and the tokens left over.
    """
    node: Group | Command = root
    path: list[str] = []
    i = 0
    while isinstance(node, Group) and i < len(tokens):
        child = node.lookup(tokens[i])
        if child is None:
            break
        node = child
        path.append(child.name)
        i += 1
    return node, path, tokens[i:]


def unknown_command(group: Group, path: list[str], token: str) -> CommandError:
    where = " ".join(path) or "the root"
    names = [n for c in group.children.values() for n in (c.name, *c.aliases)]
    close = difflib.get_close_matches(token, names, n=3)
    hint = f" Did you mean: {', '.join(' '.join([*path, c]) for c in close)}?" if close else ""
    available = ", ".join(group.children) or "none"
    return CommandError(f"Unknown command {token!r} in {where}.{hint} Available: {available}.")


def bind(command: Command, tokens: list[str], args: dict[str, Any] | None) -> dict[str, Any]:
    """Assign command-line tokens and the args object to the command's parameters."""
    values: dict[str, Any] = {}
    loose: list[str] = []

    i = 0
    options_done = False
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if options_done or not token.startswith("--"):
            loose.append(token)
            continue
        if token == "--":
            options_done = True
            continue
        key, has_value, raw = token[2:].partition("=")
        param = command.param(key)
        if param is None and key.startswith("no-") and (neg := command.param(key[3:])) and neg.is_bool:
            _assign(values, neg, False)
            continue
        if param is None:
            raise _unknown_option(command, key)
        if param.is_bool and not has_value:
            _assign(values, param, True)
            continue
        if not has_value:
            if i >= len(tokens):
                raise UsageError(f"{param.flag} needs a value")
            raw = tokens[i]
            i += 1
        _assign(values, param, _coerce(param, raw))

    open_positionals = [p for p in command.params if p.positional and p.name not in values]
    for n, param in enumerate(open_positionals):
        if not loose:
            break
        if param.is_array and n == len(open_positionals) - 1:
            for raw in loose:
                _assign(values, param, _coerce(param, raw))
            loose = []
        else:
            _assign(values, param, _coerce(param, loose.pop(0)))
    if loose:
        raise UsageError(f"unexpected argument(s): {' '.join(shlex.quote(t) for t in loose)}")

    for key, value in (args or {}).items():
        param = command.param(key)
        if param is None:
            raise _unknown_option(command, key)
        if param.name in values:
            raise UsageError(f"{param.name!r} is given both in the command line and in args")
        values[param.name] = value
    return values


def _assign(values: dict[str, Any], param: Param, value: Any) -> None:
    if param.is_array:
        items = value if isinstance(value, list) else [value]
        values.setdefault(param.name, []).extend(items)
    elif param.name in values:
        raise UsageError(f"{param.name!r} is given more than once")
    else:
        values[param.name] = value


def _coerce(param: Param, raw: str) -> Any:
    """Turn a command-line token into a value, guided by the parameter's schema.

    Strings stay strings; anything else is read as JSON when it parses, so `3`,
    `true`, `[1,2]` and `{"a":1}` arrive typed. A token for an array is one item,
    unless it is a JSON array.
    """
    schema = param.schema
    if param.is_array:
        if raw.startswith("["):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        schema = schema.get("items", {})
        for branch in param.schema.get("anyOf", []):
            if "items" in branch:
                schema = branch["items"]
    if "string" in _schema_types(schema):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _unknown_option(command: Command, key: str) -> UsageError:
    flags = [p.flag for p in command.params]
    close = difflib.get_close_matches("--" + key, flags, n=1)
    hint = f" Did you mean {close[0]}?" if close else ""
    return UsageError(f"unknown option --{key}.{hint} Options: {', '.join(flags) or 'none'}.")

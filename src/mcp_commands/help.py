"""Rendering help text: compact, CLI-style, meant to be read by a model."""

from __future__ import annotations

import json
from typing import Any

from mcp_commands.tree import Command, Group, Param

def usage(example: str) -> str:
    """How to write a command line, with `example` as the sample."""
    return (
        "Run a command with run(command, args?). The command is one line, like a shell: "
        f"the command path, then positional arguments, then --options, e.g. {example}. "
        "Values are parsed as JSON when the parameter is not a string. "
        'Repeat an array option, or pass a JSON array (--tags \'["a","b"]\'). '
        "Structured values can go in args instead, as an object keyed by parameter name."
    )


USAGE = usage('issue create "Fix login" --priority 1')


def example(group: Group) -> str | None:
    """A sample command line from the tree: the first command with a required
    argument (its path and required arguments as placeholders), else the first command."""
    first: str | None = None
    stack: list[tuple[Group, list[str]]] = [(group, [])]
    while stack:
        node, path = stack.pop(0)
        for child in node.children.values():
            if isinstance(child, Group):
                stack.append((child, [*path, child.name]))
                continue
            line = " ".join([*path, child.name])
            required = [f"<{p.name}>" for p in child.params if p.positional and p.required]
            if required:
                return " ".join([line, *required])
            first = first or line
    return first


def run_description(group: Group) -> str:
    """The run tool's description: what the server is, its top-level commands
    and how to write a command line, so each server's run tool reads as its own."""
    out = [f"{group.name}: {group.help}"] if group.help else []
    names = ", ".join(f"{c.name} …" if isinstance(c, Group) else c.name for c in group.children.values())
    line = f"Run a {group.name} command."
    if names:
        line += f" Top-level commands: {names}."
    out.append(line + ' help("<command>") shows a command\'s arguments and options.')
    sample = example(group)
    out.append(usage(sample) if sample else USAGE)
    return "\n\n".join(out)


def render(node: Group | Command, path: list[str], depth: int = 1) -> str:
    if isinstance(node, Command):
        return render_command(node, path)
    return render_group(node, path, depth)


def render_group(group: Group, path: list[str], depth: int = 1) -> str:
    out: list[str] = []
    title = " ".join(path) or group.name
    out.append(f"{title}: {group.help}" if group.help else title)
    if group.description:
        out += ["", group.description]
    if not group.children:
        out += ["", "(no commands)"]
        return "\n".join(out)

    rows: list[tuple[str, str]] = []
    _listing(group, [], max(depth, 1), rows)
    width = min(max(len(r[0]) for r in rows), 40)
    out += ["", "Commands:"]
    for left, right in rows:
        out.append(f"  {left.ljust(width)}  {right}".rstrip() if right else f"  {left}")
    prefix = " ".join(path)
    out += ["", f'Details: help("{prefix + " " if prefix else ""}<command>")']
    return "\n".join(out)


def _listing(group: Group, indent: list[str], depth: int, rows: list[tuple[str, str]]) -> None:
    pad = "  " * len(indent)
    for child in group.children.values():
        if isinstance(child, Group):
            count = _count(child)
            noun = "command" if count == 1 else "commands"
            label = f"{pad}{child.name} …"
            rows.append((label, f"{child.help}{' ' if child.help else ''}({count} {noun})"))
            if depth > 1:
                _listing(child, [*indent, child.name], depth - 1, rows)
        else:
            rows.append((f"{pad}{child.name}{_brief_usage(child)}", child.help))


def _count(group: Group) -> int:
    return sum(_count(c) if isinstance(c, Group) else 1 for c in group.children.values())


def _brief_usage(command: Command) -> str:
    return "".join(" " + _positional(p) for p in command.params if p.positional)


def _positional(p: Param) -> str:
    dots = "..." if p.is_array else ""
    return f"<{p.name}{dots}>" if p.required else f"[{p.name}{dots}]"


def render_command(command: Command, path: list[str]) -> str:
    usage = " ".join(path)
    for p in command.params:
        if p.positional:
            usage += " " + _positional(p)
    if any(not p.positional for p in command.params):
        usage += " [options]"
    out = [f"Usage: {usage}"]
    if command.aliases:
        out.append(f"Aliases: {', '.join(command.aliases)}")
    if command.help:
        out += ["", command.help]
    if command.description:
        out += ["", command.description]

    positional = [p for p in command.params if p.positional]
    options = [p for p in command.params if not p.positional]
    for heading, params in (("Arguments", positional), ("Options", options)):
        if not params:
            continue
        rows = [(p.name if p.positional else p.flag, _param_info(p)) for p in params]
        width = min(max(len(r[0]) for r in rows), 24)
        out += ["", f"{heading}:"]
        for (left, info), p in zip(rows, params):
            line = f"  {left.ljust(width)}  {info}"
            if p.description:
                line += f"  {p.description}"
            out.append(line)
    return "\n".join(out)


def _param_info(p: Param) -> str:
    info = type_label(p.schema)
    if p.required:
        info += ", required"
    elif "default" in p.schema and p.schema["default"] is not None:
        info += f", default {json.dumps(p.schema['default'])}"
    constraints = [
        f"{word} {p.schema[key]}"
        for key, word in (("minimum", ">="), ("maximum", "<="), ("minLength", "min length"), ("maxLength", "max length"))
        if key in p.schema
    ]
    if "pattern" in p.schema:
        constraints.append(f"matches /{p.schema['pattern']}/")
    if constraints:
        info += ", " + ", ".join(constraints)
    return f"({info})"


def type_label(schema: dict[str, Any]) -> str:
    if "enum" in schema:
        return "|".join(json.dumps(v) if not isinstance(v, str) else v for v in schema["enum"])
    if "const" in schema:
        return json.dumps(schema["const"])
    branches = schema.get("anyOf") or schema.get("oneOf")
    if branches:
        labels = [type_label(b) for b in branches if b.get("type") != "null"]
        return "|".join(dict.fromkeys(labels)) or "null"
    t = schema.get("type")
    if isinstance(t, list):
        return "|".join(x for x in t if x != "null")
    if t == "array":
        item = type_label(schema.get("items", {}))
        return f"({item})[]" if "|" in item else f"{item}[]"
    if t == "object" and "properties" in schema:
        fields = ", ".join(
            f"{k}{'' if k in schema.get('required', []) else '?'}: {type_label(v)}"
            for k, v in schema["properties"].items()
        )
        return "{" + fields + "}"
    return t or "any"

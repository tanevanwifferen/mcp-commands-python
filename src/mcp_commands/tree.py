"""The command tree: groups that hold commands and other groups."""

from __future__ import annotations

import inspect
import re
import typing
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar, overload

import pydantic
from mcp.server.mcpserver import Context
from pydantic.fields import FieldInfo

from mcp_commands.errors import UsageError

F = TypeVar("F", bound=Callable[..., Any])

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class Param:
    """One parameter of a command, as the AI sees it."""

    name: str
    schema: dict[str, Any]
    """JSON schema of the value, with `$ref`s resolved."""
    required: bool
    positional: bool
    description: str | None

    @property
    def flag(self) -> str:
        return "--" + self.name.replace("_", "-")

    @property
    def is_bool(self) -> bool:
        return _schema_types(self.schema) == {"boolean"}

    @property
    def is_array(self) -> bool:
        return "array" in _schema_types(self.schema)


class Command:
    """A leaf of the tree: a function the AI can run."""

    def __init__(
        self,
        fn: Callable[..., Any],
        name: str | None = None,
        *,
        help: str | None = None,
        description: str | None = None,
        aliases: Iterable[str] = (),
    ) -> None:
        self.fn = fn
        self.name = _check_name(name or fn.__name__.replace("_", "-"))
        self.aliases = tuple(_check_name(a) for a in aliases)

        summary, body, arg_docs = _parse_docstring(inspect.getdoc(fn) or "")
        self.help = help or summary
        self.description = description if description is not None else body

        sig = inspect.signature(fn)
        hints = typing.get_type_hints(fn, include_extras=True)
        fields: dict[str, Any] = {}
        positional: set[str] = set()
        self.context_param: str | None = None
        for p in sig.parameters.values():
            annotation = hints.get(p.name, Any)
            if _is_context(annotation):
                self.context_param = p.name
                continue
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                raise TypeError(f"command {self.name!r}: *args and **kwargs are not supported")
            if p.kind is not p.KEYWORD_ONLY:
                positional.add(p.name)
            default = p.default
            if isinstance(default, FieldInfo):
                fields[p.name] = (annotation, default)
            elif default is p.empty:
                fields[p.name] = (annotation, ...)
            else:
                fields[p.name] = (annotation, default)

        self.model: type[pydantic.BaseModel] = pydantic.create_model(f"{self.name}_params", **fields)
        schema = self.model.model_json_schema()
        defs = schema.get("$defs", {})
        required = set(schema.get("required", []))
        self.params: list[Param] = []
        for name, prop in schema.get("properties", {}).items():
            prop = _deref(prop, defs)
            self.params.append(
                Param(
                    name=name,
                    schema=prop,
                    required=name in required,
                    positional=name in positional,
                    description=prop.get("description") or arg_docs.get(name),
                )
            )

    def param(self, key: str) -> Param | None:
        key = key.replace("-", "_")
        return next((p for p in self.params if p.name == key), None)

    async def invoke(self, values: dict[str, Any], ctx: Context | None = None) -> Any:
        try:
            validated = self.model.model_validate(values)
        except pydantic.ValidationError as e:
            raise UsageError(_format_validation_error(e, self)) from None
        kwargs = {p.name: getattr(validated, p.name) for p in self.params}
        if self.context_param is not None:
            kwargs[self.context_param] = ctx
        result = self.fn(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result


class Group:
    """An inner node of the tree: a named set of commands and subgroups."""

    def __init__(
        self,
        name: str,
        help: str | None = None,
        *,
        description: str | None = None,
        aliases: Iterable[str] = (),
    ) -> None:
        self.name = name
        self.help = help or ""
        self.description = description or ""
        self.aliases = tuple(_check_name(a) for a in aliases)
        self.children: dict[str, Group | Command] = {}

    def group(
        self,
        name: str,
        help: str | None = None,
        *,
        description: str | None = None,
        aliases: Iterable[str] = (),
    ) -> Group:
        """Create a subgroup, e.g. `issue = app.group("issue", "Manage issues")`."""
        return self.add(Group(_check_name(name), help, description=description, aliases=aliases))

    @overload
    def command(self, fn: F, /) -> F: ...
    @overload
    def command(
        self,
        name: str | None = None,
        *,
        help: str | None = None,
        description: str | None = None,
        aliases: Iterable[str] = (),
    ) -> Callable[[F], F]: ...
    def command(
        self,
        name: str | Callable[..., Any] | None = None,
        *,
        help: str | None = None,
        description: str | None = None,
        aliases: Iterable[str] = (),
    ) -> Any:
        """Register a function as a command of this group.

        The name defaults to the function name (underscores become dashes); the help
        text and parameter descriptions default to the docstring.
        """
        if callable(name):
            self.add(Command(name))
            return name

        def decorator(fn: F) -> F:
            self.add(Command(fn, name, help=help, description=description, aliases=aliases))
            return fn

        return decorator

    def add(self, node: G) -> G:
        for key in (node.name, *node.aliases):
            if self.lookup(key) is not None:
                raise ValueError(f"{self.name!r} already has a command or group named {key!r}")
        self.children[node.name] = node
        return node

    def lookup(self, token: str) -> Group | Command | None:
        if token in self.children:
            return self.children[token]
        return next((c for c in self.children.values() if token in c.aliases), None)


G = TypeVar("G", Group, Command)


def _check_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid command name {name!r}: use letters, digits, '-' and '_'")
    return name


def _is_context(annotation: Any) -> bool:
    candidates = [annotation, *typing.get_args(annotation)]
    return any(inspect.isclass(a) and issubclass(a, Context) for a in candidates)


def _schema_types(schema: dict[str, Any]) -> set[str]:
    t = schema.get("type")
    if isinstance(t, str):
        types = {t}
    elif isinstance(t, list):
        types = set(t)
    else:
        types = set()
    for branch in schema.get("anyOf", []) + schema.get("oneOf", []):
        types |= _schema_types(branch)
    if "enum" in schema:
        types |= {_json_type(v) for v in schema["enum"]}
    return types - {"null"}


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    return "string"


def _deref(schema: Any, defs: dict[str, Any]) -> Any:
    if isinstance(schema, list):
        return [_deref(s, defs) for s in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        target = defs.get(schema["$ref"].rsplit("/", 1)[-1], {})
        rest = {k: v for k, v in schema.items() if k != "$ref"}
        return _deref({**target, **rest}, defs)
    return {k: _deref(v, defs) for k, v in schema.items()}


_SECTION_RE = re.compile(r"^(Args|Arguments|Parameters|Returns|Raises|Yields|Examples?|Notes?):\s*$")
_ARG_RE = re.compile(r"^\s+(\*{0,2}\w+)\s*(?:\(.*?\))?\s*:\s*(.*)$")


def _parse_docstring(doc: str) -> tuple[str, str, dict[str, str]]:
    """Split a Google-style docstring into (summary, body, {arg: description})."""
    lines = doc.splitlines()
    body: list[str] = []
    args: dict[str, str] = {}
    section: str | None = None
    current: str | None = None
    for line in lines:
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1)
            current = None
            continue
        if section in ("Args", "Arguments", "Parameters"):
            am = _ARG_RE.match(line)
            if am:
                current = am.group(1).lstrip("*")
                args[current] = am.group(2).strip()
            elif current and line.strip():
                args[current] += " " + line.strip()
            elif line.strip() and not line.startswith(" "):
                section = None
                body.append(line)
        elif section is None:
            body.append(line)
    text = "\n".join(body).strip()
    summary, _, rest = text.partition("\n\n")
    return " ".join(summary.split()), rest.strip(), args


def _format_validation_error(error: pydantic.ValidationError, command: Command) -> str:
    problems = []
    for e in error.errors():
        name = str(e["loc"][0]) if e["loc"] else "?"
        param = command.param(name)
        label = (name if param and param.positional else param.flag) if param else name
        where = ".".join(str(x) for x in e["loc"][1:])
        if e["type"] == "missing" and not where:
            problems.append(f"missing required argument <{name}>")
        elif e["type"] == "missing":
            problems.append(f"{label}.{where}: missing required property")
        else:
            problems.append(f"{label}{'.' + where if where else ''}: {e['msg']} (got {_describe(e.get('input'))})")
    return "; ".join(problems)


def _describe(value: Any) -> str:
    """The JSON type of a value for error messages, with an object's keys, rather than the value itself."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple, set)):
        return "array"
    if isinstance(value, dict):
        if not value:
            return "object"
        keys = [str(k) for k in value]
        return "object {" + ", ".join(keys[:10] + (["…"] if len(keys) > 10 else [])) + "}"
    return type(value).__name__

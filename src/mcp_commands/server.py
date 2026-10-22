"""CliServer: an MCP server that exposes a command tree through two tools."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Annotated, Any, Literal

import pydantic
import pydantic_core
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import CallToolResult, ContentBlock, TextContent, ToolAnnotations

from mcp_commands import help as helptext
from mcp_commands.errors import CommandError, UsageError
from mcp_commands.parse import bind, split_path, tokenize, unknown_command, walk
from mcp_commands.tree import Command, Group

logger = logging.getLogger(__name__)

_CONTENT_TYPES = tuple(getattr(ContentBlock, "__args__", ()))


class CliServer(Group):
    """An MCP server whose commands are discovered with `help` and executed with `run`.

    Build the tree like a CLI:

        app = CliServer("beads", "Git-backed issue tracker")
        issue = app.group("issue", "Manage issues")

        @issue.command()
        def create(title: str, priority: int = 2, deps: list[str] | None = None) -> dict:
            '''Create an issue.'''

        app.run()

    The model then sees two tools: `help(path)` and `run(command, args)`.
    """

    def __init__(
        self,
        name: str,
        description: str | None = None,
        *,
        instructions: str | None = None,
        version: str = "",
        help_tool: str = "help",
        run_tool: str = "run",
        list_commands_in_description: bool = True,
        **mcp_kwargs: Any,
    ) -> None:
        """
        Args:
            name: Server name, also the title of the root help page.
            description: What the server is for; the first thing the model reads.
            instructions: MCP instructions sent at initialization. Defaults to the
                description plus a short explanation of help and run.
            version: Server version.
            help_tool: Name of the discovery tool.
            run_tool: Name of the execution tool.
            list_commands_in_description: Put the top-level command listing in the
                help tool's description, so the model knows the commands without a call.
            **mcp_kwargs: Passed on to `MCPServer` (lifespan, auth, ...).
        """
        super().__init__(name, description)
        self.help_tool_name = help_tool
        self.run_tool_name = run_tool
        self.list_commands_in_description = list_commands_in_description
        if instructions is None:
            instructions = (
                f"{description + chr(10) + chr(10) if description else ''}"
                f"This server is a command tree, used like a CLI. Call {help_tool}() to list the commands, "
                f'{help_tool}("<command path>") for a group or a command\'s arguments, '
                f'and {run_tool}("<command path> <arguments> --option value") to execute one.'
            )
        self.mcp = MCPServer(name=name, instructions=instructions, version=version, **mcp_kwargs)
        """The underlying MCPServer; add resources and prompts to it as usual."""

    # -- Using the tree directly (tests, other front ends) --

    def help_text(self, path: str = "", depth: int = 1) -> str:
        """The help page for a path: "" for the root, "issue", "issue create", ..."""
        tokens = split_path(path)
        node, found, rest = walk(self, tokens)
        if rest:
            assert isinstance(node, Group)
            raise unknown_command(node, found, rest[0])
        return helptext.render(node, found, depth)

    async def execute(self, command: str, args: dict[str, Any] | None = None, ctx: Context | None = None) -> Any:
        """Parse and run one command line, returning what the command returned."""
        tokens = tokenize(command)
        node, path, rest = walk(self, tokens)
        options = rest[: rest.index("--")] if "--" in rest else rest
        if "--help" in options or "-h" in options:
            return helptext.render(node, path)
        if isinstance(node, Group):
            if rest:
                raise unknown_command(node, path, rest[0])
            what = f"{' '.join(path)!r} is a group, not a command" if path else "No command given"
            raise CommandError(f"{what}.\n\n{helptext.render_group(node, path)}")
        try:
            values = bind(node, rest, args)
            return await node.invoke(values, ctx)
        except UsageError as e:
            raise UsageError(f"{e}\n\n{helptext.render_command(node, path)}") from None

    # -- MCP --

    @property
    def server(self) -> MCPServer:
        """The MCPServer with the help and run tools registered for the current tree."""
        self._register_tools()
        return self.mcp

    def run(self, transport: Literal["stdio", "sse", "streamable-http"] = "stdio", **kwargs: Any) -> None:
        self.server.run(transport, **kwargs)

    async def run_stdio_async(self) -> None:
        await self.server.run_stdio_async()

    def streamable_http_app(self, **kwargs: Any) -> Any:
        return self.server.streamable_http_app(**kwargs)

    def _register_tools(self) -> None:
        for name in (self.help_tool_name, self.run_tool_name):
            try:
                self.mcp.remove_tool(name)
            except Exception:
                pass

        async def help(
            path: Annotated[
                str,
                pydantic.Field(description='Command path, e.g. "" for the root, "issue", "issue create".'),
            ] = "",
            depth: Annotated[
                int, pydantic.Field(ge=1, le=10, description="How many levels of nested groups to list.")
            ] = 1,
        ) -> CallToolResult:
            try:
                return _text(self.help_text(path, depth))
            except CommandError as e:
                return _text(str(e), error=True)

        async def run(
            command: Annotated[
                str,
                pydantic.Field(description='One command line: path, arguments, options. E.g. issue create "Title" --priority 1'),
            ],
            args: Annotated[
                dict[str, Any] | None,
                pydantic.Field(description="Optional parameter values by name, for structured input."),
            ] = None,
            ctx: Context | None = None,
        ) -> CallToolResult:
            try:
                return _to_result(await self.execute(command, args, ctx))
            except CommandError as e:
                return _text(str(e), error=True)
            except Exception as e:
                logger.exception("command %r failed", command)
                return _text(f"{type(e).__name__}: {e}", error=True)

        help_description = (
            "List the commands of this server and show how to call them. "
            'help() lists the top-level commands, help("<group>") a group\'s commands, '
            'help("<group> <command>") a command\'s arguments and options. '
            "Raise depth to list nested groups too."
        )
        if self.help:
            help_description = f"{self.name}: {self.help}\n\n{help_description}"
        if self.list_commands_in_description and self.children:
            help_description += "\n\n" + helptext.render_group(self, [], 1).split("\n\n", 1)[-1]

        self.mcp.add_tool(
            help,
            name=self.help_tool_name,
            description=help_description,
            annotations=ToolAnnotations(read_only_hint=True),
        )
        self.mcp.add_tool(run, name=self.run_tool_name, description=helptext.USAGE)


def _text(text: str, error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=error)


def _to_result(value: Any) -> CallToolResult:
    if isinstance(value, CallToolResult):
        return value
    if _CONTENT_TYPES and isinstance(value, _CONTENT_TYPES):
        return CallToolResult(content=[value])
    if isinstance(value, Sequence) and value and all(isinstance(v, _CONTENT_TYPES) for v in value):
        return CallToolResult(content=list(value))
    if value is None:
        return _text("OK")
    if isinstance(value, str):
        return _text(value)
    return _text(json.dumps(pydantic_core.to_jsonable_python(value), ensure_ascii=False, separators=(",", ":")))

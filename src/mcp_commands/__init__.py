"""MCP servers shaped like a CLI: a command tree behind two tools, `help` and `run`."""

from mcp_commands.errors import CommandError, UsageError
from mcp_commands.server import CliServer
from mcp_commands.tree import Command, Group, Param

__all__ = ["CliServer", "Command", "CommandError", "Group", "Param", "UsageError"]

# mcp-commands

**MCP servers shaped like a CLI.** You define a tree of commands, like `git` or `beads`, and the
model sees only two tools: `help` and `run`.

```
issue create "Fix login" 1 bd-3 --description "Users get logged out after 5 minutes"
```

A regular MCP server sends the model a schema for every tool. That costs context before the
model has done anything, and the cost grows with each tool you add. mcp-commands keeps the
tool list at two however many commands you have. The model reads help for a command only when
it is about to use it, the way you'd run `--help`.

Also available for TypeScript: [mcp-commands-typescript](https://github.com/tanevanwifferen/mcp-commands-typescript)
and Go: [mcp-commands-go](https://github.com/tanevanwifferen/mcp-commands-go).

## Install

```sh
pip install mcp-commands      # or: uv add mcp-commands
```

Requires Python 3.10+ and the official [`mcp`](https://github.com/modelcontextprotocol/python-sdk) SDK, version 2 or later.

## Example

```python
from typing import Annotated

from pydantic import Field
from mcp_commands import CliServer, CommandError

app = CliServer("beads", "Issue tracker with dependencies. Use it to plan work and find what is ready next.")

issue = app.group("issue", "Create, update and inspect issues")


@issue.command()
def create(
    title: str,
    priority: Annotated[int, Field(ge=0, le=4)] = 2,
    deps: list[str] | None = None,
    *,
    description: str = "",
) -> dict:
    """Create an issue.

    Args:
        title: Short summary of the work.
        priority: 0 is the most urgent, 4 the least.
        deps: IDs of issues that must be closed first.
        description: Longer explanation, acceptance criteria.
    """
    ...


@issue.command(name="list", aliases=["ls"])
def list_issues(*, all: bool = False) -> list[dict]:
    """List issues, open ones by default."""
    ...


@app.command()
def ready() -> list[dict]:
    """Open issues whose dependencies are all closed."""
    ...


if __name__ == "__main__":
    app.run()  # stdio; also app.run("streamable-http") or app.streamable_http_app()
```

The API follows `MCPServer` (formerly FastMCP):

- Parameter types and validation come from type hints, via pydantic.
- Descriptions come from the Google-style docstring, or from `Field(description=...)`.
- Parameters before `*` are **arguments**: positional, in order. Parameters after `*` are
  **options** and can only be passed as `--name value`.
- Commands can be sync or async. A parameter annotated with `Context` gets the MCP request context.
- A command can return a `str`, any JSON-serializable value (pydantic models included), or
  `None` (shown as `OK`). It can also return MCP content blocks or a `CallToolResult`.
- `raise CommandError("...")` fails the call with exactly that message.

Groups nest as deep as you like: `issue.group("label", "Manage labels")`.

## What the model sees

**`help(path = "", depth = 1)`**. The top-level listing is already in this tool's
description and in the server's MCP instructions, so the model usually doesn't need to call
it for the root:

```
beads: Issue tracker with dependencies. Use it to plan work and find what is ready next.

Commands:
  issue …  Create, update and inspect issues (4 commands)
  ready    Open issues whose dependencies are all closed.
```

`help("issue create")`:

```
Usage: issue create <title> [priority] [deps...] [options]

Create an issue.

Arguments:
  title     (string, required)  Short summary of the work.
  priority  (integer, default 2, >= 0, <= 4)  0 is the most urgent, 4 the least.
  deps      (string[])  IDs of issues that must be closed first.

Options:
  --description  (string, default "")  Longer explanation, acceptance criteria.
```

**`run(command, args?)`** executes one command line. `args` is an optional object of
parameter values, for input that is awkward to quote.

Both tools are titled and described per server (`beads: run a command`, and a `run`
description that starts with the server's name, description and top-level commands, with an
example taken from its own tree), so a client with several mcp-commands servers connected can
tell their `run` tools apart.

## Command-line rules

- The first words are the command path. Aliases work (`issue ls`).
- Arguments are positional, in declaration order. They can also be given as `--name value`.
- Options take `--name value` or `--name=value`. Underscores in names become dashes.
- Booleans: `--flag` sets true, `--no-flag` sets false.
- Arrays: an array argument in the last position takes the remaining words (`issue create t 1 bd-1 bd-2`).
  Otherwise repeat the option (`--deps a --deps b`) or pass JSON (`--deps '["a","b"]'`).
- Quoting works like a POSIX shell. `--` ends options. `--help` shows the command's help.
- Words for string parameters stay strings, so `123` stays `"123"`. Words for other types are
  parsed as JSON (`3`, `true`, `{"a": 1}`).

Errors are written for the model to act on:

```
priority: Input should be less than or equal to 4 (got integer)

Usage: issue create <title> [priority] [deps...] [options]
...
```

```
Unknown command 'isue' in the root. Did you mean: issue? Available: issue, ready.
```

## Options

```python
CliServer(
    name,
    description=None,
    instructions=None,                  # defaults to the description plus how to use help/run
    version="",
    help_tool="help",                   # tool names
    run_tool="run",
    list_commands_in_description=True,  # put the root listing in the help tool's description
    **mcp_kwargs,                       # passed on to MCPServer: lifespan, auth, ...
)
```

`app.mcp` is the underlying `MCPServer`, so you can add resources and prompts to it as usual.

## Testing without MCP

```python
await app.execute('issue create "Fix login" --priority 1')  # returns what the command returned
app.help_text("issue create")
```

## Development

```sh
uv run pytest
uv run examples/beads.py
```

## License

MIT

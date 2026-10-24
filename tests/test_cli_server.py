from typing import Any, Literal

import pytest
from mcp import Client
from pydantic import BaseModel

from mcp_commands import CliServer, CommandError, UsageError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Where(BaseModel):
    state: Literal["open", "closed"]
    tags: list[str] = []


def make_app() -> CliServer:
    app = CliServer("beads", "Issue tracker")
    issue = app.group("issue", "Manage issues")

    @issue.command()
    def create(title: str, priority: int = 2, deps: list[str] | None = None, *, draft: bool = False) -> dict:
        """Create an issue.

        Args:
            title: Short summary.
            priority: 0-4.
        """
        return {"title": title, "priority": priority, "deps": deps, "draft": draft}

    @issue.command(aliases=["rm"])
    async def delete(id: str) -> str:
        """Delete an issue."""
        if id == "bd-0":
            raise CommandError("bd-0 is protected")
        return f"deleted {id}"

    label = issue.group("label", "Manage labels")

    @label.command()
    def add(id: str, labels: list[str]) -> list[str]:
        """Add labels."""
        return labels

    @app.command(name="filter")
    def filter_issues(*, where: Where) -> dict:
        """Filter issues."""
        return where.model_dump()

    @app.command()
    def ready() -> None:
        """Show ready work."""

    return app


async def test_positional_and_options() -> None:
    app = make_app()
    assert await app.execute('issue create "Fix login" 1 bd-1 bd-2') == {
        "title": "Fix login",
        "priority": 1,
        "deps": ["bd-1", "bd-2"],
        "draft": False,
    }
    result = await app.execute("issue create x --priority=3 --deps a --deps b --draft")
    assert result == {"title": "x", "priority": 3, "deps": ["a", "b"], "draft": True}
    result = await app.execute("issue create x --deps '[\"a\",\"b\"]' --no-draft")
    assert result["deps"] == ["a", "b"] and result["draft"] is False


async def test_args_object() -> None:
    app = make_app()
    result = await app.execute("issue create", {"title": "t", "deps": ["a"], "draft": True})
    assert result == {"title": "t", "priority": 2, "deps": ["a"], "draft": True}
    with pytest.raises(UsageError, match="both"):
        await app.execute("issue create t", {"title": "u"})


async def test_numbers_stay_strings_for_string_params() -> None:
    app = make_app()
    assert (await app.execute("issue create 123"))["title"] == "123"


async def test_nested_group_and_variadic() -> None:
    app = make_app()
    assert await app.execute("issue label add bd-1 a b c") == ["a", "b", "c"]


async def test_alias_and_async_and_command_error() -> None:
    app = make_app()
    assert await app.execute("issue rm bd-3") == "deleted bd-3"
    with pytest.raises(CommandError, match="protected"):
        await app.execute("issue delete bd-0")


async def test_errors_explain_themselves() -> None:
    app = make_app()
    with pytest.raises(CommandError, match="Did you mean: issue create"):
        await app.execute("issue craete x")
    with pytest.raises(UsageError, match="missing required argument <title>") as e:
        await app.execute("issue create")
    assert "Usage: issue create <title>" in str(e.value)
    with pytest.raises(UsageError, match=r"Did you mean --priority"):
        await app.execute("issue create x --prio 1")
    with pytest.raises(UsageError, match="valid integer"):
        await app.execute("issue create x high")
    with pytest.raises(CommandError, match="is a group"):
        await app.execute("issue")


def test_help_pages() -> None:
    app = make_app()
    root = app.help_text()
    assert "issue …" in root and "(3 commands)" in root and "ready" in root
    assert "label" not in root
    assert "  label …" in app.help_text("", depth=2)
    cmd = app.help_text("issue create")
    assert "Usage: issue create <title> [priority] [deps...] [options]" in cmd
    assert "title" in cmd and "Short summary." in cmd and "--draft" in cmd
    assert app.help_text("issue/label") == app.help_text("issue > label")


async def test_inline_help() -> None:
    app = make_app()
    assert (await app.execute("issue create --help")).startswith("Usage: issue create")


async def test_over_mcp() -> None:
    app = make_app()
    async with Client(app.server) as client:
        tools = (await client.list_tools()).tools
        assert [t.name for t in tools] == ["help", "run"]
        help_tool = next(t for t in tools if t.name == "help")
        assert "issue …" in (help_tool.description or "")
        run_tool = next(t for t in tools if t.name == "run")
        assert run_tool.title == "beads: run a command"
        assert run_tool.description.startswith("beads: ")
        assert "Top-level commands: issue …, filter, ready." in run_tool.description
        assert "e.g. issue create <title>." in run_tool.description
        assert "beads command line" in run_tool.input_schema["properties"]["command"]["description"]

        result = await client.call_tool("help", {"path": "issue"})
        assert not result.is_error and "create <title>" in _text(result)

        result = await client.call_tool("run", {"command": "issue create 'A b' --priority 0"})
        assert not result.is_error
        assert _text(result) == '{"title":"A b","priority":0,"deps":null,"draft":false}'

        result = await client.call_tool("run", {"command": "issue delete bd-0"})
        assert result.is_error and _text(result) == "bd-0 is protected"

        result = await client.call_tool("run", {"command": "ready"})
        assert _text(result) == "OK"


def _text(result: Any) -> str:
    return result.content[0].text


async def test_errors_name_types_paths_and_keys() -> None:
    app = make_app()
    with pytest.raises(UsageError, match=r"^priority: .* \(got string\)"):
        await app.execute("issue create x high")
    with pytest.raises(UsageError, match=r"^deps\.1: .* \(got integer\); deps\.2: .* \(got boolean\)\n"):
        await app.execute("issue create x", {"deps": ["a", 1, True]})
    with pytest.raises(UsageError, match=r"^title: .* \(got object \{en, nl\}\)"):
        await app.execute("issue create", {"title": {"en": "x", "nl": "y"}})
    with pytest.raises(UsageError, match=r"^--where\.state: missing required property"):
        await app.execute("filter", {"where": {"tags": ["a"]}})

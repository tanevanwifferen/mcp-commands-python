"""A toy issue tracker in the style of beads, served as a command tree.

Run it with: uv run examples/beads.py
"""

from enum import Enum
from typing import Annotated

from pydantic import Field

from mcp_commands import CliServer, CommandError

app = CliServer(
    "beads",
    "Issue tracker with dependencies. Use it to plan work and find what is ready to do next.",
)

ISSUES: dict[str, dict] = {}


class Status(str, Enum):
    open = "open"
    in_progress = "in_progress"
    closed = "closed"


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
    for dep in deps or []:
        if dep not in ISSUES:
            raise CommandError(f"unknown dependency {dep!r}")
    id = f"bd-{len(ISSUES) + 1}"
    ISSUES[id] = {
        "id": id,
        "title": title,
        "priority": priority,
        "deps": deps or [],
        "description": description,
        "status": Status.open,
    }
    return ISSUES[id]


@issue.command(aliases=["get"])
def show(id: str) -> dict:
    """Show one issue.

    Args:
        id: Issue ID, e.g. bd-1.
    """
    if id not in ISSUES:
        raise CommandError(f"no issue {id!r}")
    return ISSUES[id]


@issue.command()
def update(id: str, *, status: Status | None = None, priority: int | None = None) -> dict:
    """Change the status or priority of an issue."""
    found = show(id)
    if status is not None:
        found["status"] = status
    if priority is not None:
        found["priority"] = priority
    return found


@issue.command(name="list", aliases=["ls"])
def list_issues(*, status: Status | None = None, all: bool = False) -> list[dict]:
    """List issues, open ones by default.

    Args:
        status: Only issues with this status.
        all: Include closed issues.
    """
    return [
        i
        for i in ISSUES.values()
        if (status is None or i["status"] == status) and (all or i["status"] != Status.closed)
    ]


@app.command()
def ready() -> list[dict]:
    """Open issues whose dependencies are all closed, most urgent first."""
    open_ = [i for i in ISSUES.values() if i["status"] == Status.open]
    ok = [i for i in open_ if all(ISSUES[d]["status"] == Status.closed for d in i["deps"])]
    return sorted(ok, key=lambda i: i["priority"])


if __name__ == "__main__":
    app.run()

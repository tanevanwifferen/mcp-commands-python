class CommandError(Exception):
    """An error the AI should see as-is: a bad command line, or a refusal from a command.

    Raise it from a command to fail with a clean message and no traceback.
    """


class UsageError(CommandError):
    """The command line does not fit the command; the command's help is appended."""

import difflib

ROLES = {"guest": 0, "member": 1, "owner": 2}


class CompileError(Exception):
    """Помилка в коді мови: з номером рядка і підказкою."""

    def __init__(self, line, msg, hint=None):
        super().__init__(f"рядок {line}: {msg}" + (f"\n  підказка: {hint}" if hint else ""))


class UserError(Exception):
    """Текст показується користувачу бота як є."""


def suggest(word, options):
    m = difflib.get_close_matches(word, list(options), n=1)
    return f"може, «{m[0]}»?" if m else None
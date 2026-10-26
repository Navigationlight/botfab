import asyncio
import logging
import sys

from .adapters.console import console
from .compiler import check
from .runtime import Runtime


def main():
    logging.basicConfig(level=logging.WARNING)
    if len(sys.argv) < 3 or sys.argv[1] not in ("run", "check"):
        print("Використання: python -m botfab run файл.bfab")
        return
    src = open(sys.argv[2], encoding="utf-8").read()
    py, errors = check(src)
    if errors:
        print("Помилки:\n" + "\n".join(errors))
        sys.exit(1)
    if sys.argv[1] == "check":
        print("Код правильний ✅")
        return
    ns = {"Runtime": Runtime}
    exec(py, ns)
    asyncio.run(console(ns["app"]))
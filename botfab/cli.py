import asyncio
import logging
import os
import sys

from .adapters.console import console
from .compiler import check
from .runtime import Runtime


def main():
    logging.basicConfig(level=logging.WARNING)
    modes = ("run", "check", "telegram")
    if len(sys.argv) < 3 or sys.argv[1] not in modes:
        print("Використання: python -m botfab run|check|telegram файл.bfab")
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
    if sys.argv[1] == "telegram":
        token = os.environ.get("BOT_TOKEN")
        if not token:
            print("Не знайдено BOT_TOKEN. Спочатку: $env:BOT_TOKEN = \"токен\"")
            sys.exit(1)
        from .adapters.telegram_bot import run_telegram
        run_telegram(ns["app"], token)
        return
    asyncio.run(console(ns["app"]))
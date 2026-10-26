import asyncio
import sys


async def console(app):
    async def notifier(uid, msg):
        print(f"  [нагадування для {uid}] {msg.text}")

    app.notifier = notifier
    app.scheduler.start()
    print(f"{app.name} працює. Формат: <id> /команда ...   (Ctrl+Z, Enter: вихід)")
    if app.scheduler.jobs:
        print(f"Завдань за розкладом: {len(app.scheduler.jobs)}. "
              "Напиши !jobs, щоб виконати їх зараз (для перевірки).")
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            break
        if line.strip() == "!jobs":
            await app.scheduler.run_all()
            continue
        uid, _, text = line.strip().partition(" ")
        if not uid.isdigit():
            print("початок рядка має бути id користувача")
            continue
        for msg in await app.handle(int(uid), text):
            print("  бот:", msg)
            for row in msg.buttons:
                print("      ", "  ".join(f"[{label} → {cmd}]" for label, cmd in row))
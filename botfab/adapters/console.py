import asyncio
import sys


async def console(app):
    print(f"{app.name} працює. Формат: <id> /команда ...   (Ctrl+D вихід)")
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            break
        uid, _, text = line.strip().partition(" ")
        if not uid.isdigit():
            print("початок рядка має бути id користувача")
            continue
        for msg in await app.handle(int(uid), text):
            print("  бот:", msg)
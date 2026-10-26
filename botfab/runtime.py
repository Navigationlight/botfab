import asyncio
import logging
import sqlite3
import time
import uuid
from collections import defaultdict, deque

from .common import ROLES, UserError

log = logging.getLogger("botfab")


class Ctx:
    """Відповіді збираються тут; адаптер платформи сам вирішує, як їх показати."""

    def __init__(self, user_id, role):
        self.user_id, self.role, self.out = user_id, role, []

    async def reply(self, text):
        self.out.append(text)

    async def show(self, rows):
        self.out.append("\n".join("• " + " | ".join(map(str, r)) for r in rows)
                        if rows else "Нічого не знайдено.")


class Store:
    def __init__(self, app, name, fields):
        self.app, self.name, self.fields = app, name, fields
        cols = ", ".join(f"{f} TEXT" for f in fields)
        app._exec(f"CREATE TABLE IF NOT EXISTS {name} (id INTEGER PRIMARY KEY, {cols}, _hidden INTEGER DEFAULT 0)")

    async def add(self, **v):
        ph = ",".join("?" * len(v))
        await self.app.run(f"INSERT INTO {self.name} ({','.join(v)}) VALUES ({ph})", list(v.values()))

    async def search(self, cols, text):
        where = " OR ".join(f"{c} LIKE ?" for c in cols)
        return await self.app.run(
            f"SELECT {','.join(self.fields)} FROM {self.name} WHERE _hidden=0 AND ({where}) LIMIT 20",
            [f"%{text}%"] * len(cols))


class Runtime:
    def __init__(self, name, db):
        self.name, self.roles, self.commands = name, {}, {}
        self.conn = sqlite3.connect(db, check_same_thread=False)
        self._lock = asyncio.Lock()
        self._hits = defaultdict(lambda: deque(maxlen=5))

    def store(self, name, fields):
        return Store(self, name, fields)

    def command(self, name, args, role):
        def deco(fn):
            self.commands[name] = (args, role, fn)
            return fn
        return deco

    def _exec(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.fetchall()

    async def run(self, sql, params=()):
        # база не блокує бота: запит іде в окремому потоці, запити йдуть по черзі
        async with self._lock:
            return await asyncio.to_thread(self._exec, sql, params)

    def _allowed(self, uid):  # не більше 5 команд за 10 с
        q, now = self._hits[uid], time.monotonic()
        if len(q) == q.maxlen and now - q[0] < 10:
            return False
        q.append(now)
        return True

    async def handle(self, uid, text):
        ctx = Ctx(uid, self.roles.get(uid, "guest"))
        trace = uuid.uuid4().hex[:6]
        try:
            if not self._allowed(uid):
                raise UserError("Забагато запитів, зачекай кілька секунд.")
            cmd, _, rest = text.strip().lstrip("/").partition(" ")
            if cmd not in self.commands:
                raise UserError("Невідома команда. Є: " + ", ".join("/" + c for c in self.commands))
            args, role, fn = self.commands[cmd]
            if ROLES[ctx.role] < ROLES[role]:
                log.warning("[%s] відмова: user=%s cmd=%s role=%s", trace, uid, cmd, ctx.role)
                raise UserError("Недостатньо прав для цієї команди.")
            parts = rest.split(None, len(args) - 1) if args else []
            if len(parts) != len(args):
                raise UserError(f"Формат: /{cmd} " + " ".join(f"<{a}>" for a in args))
            await asyncio.wait_for(fn(ctx, *parts), timeout=10)
        except UserError as e:
            ctx.out.append(str(e))
        except asyncio.TimeoutError:
            log.error("[%s] таймаут cmd=%s", trace, text)
            ctx.out.append(f"Це забирає надто багато часу. Код: {trace}")
        except Exception:
            log.exception("[%s] збій cmd=%s", trace, text)  # деталі лише в лог
            ctx.out.append(f"Щось пішло не так. Код помилки: {trace}")
        return ctx.out
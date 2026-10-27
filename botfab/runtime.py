import asyncio
import datetime as dt
import logging
import sqlite3
import time
import uuid
from collections import defaultdict, deque

from .common import ROLES, UserError
from .scheduler import Scheduler

log = logging.getLogger("botfab")


class Msg:
    """Одна відповідь бота: текст + кнопки (рядки пар «підпис, команда»)."""

    def __init__(self, text, buttons=None):
        self.text, self.buttons = text, buttons or []

    def __str__(self):
        return self.text


def q(name):
    """Назва таблиці/поля в лапках для SQL (захист від зарезервованих слів)."""
    return '"' + name.replace('"', '""') + '"'


def rows_text(rows):
    """Перший стовпець кожного рядка це id запису: показуємо його як #id."""
    return ("\n".join(f"• #{r[0]} " + " | ".join(map(str, r[1:])) for r in rows)
            if rows else "Нічого не знайдено.")


class Ctx:
    """Відповіді збираються тут; адаптер платформи сам вирішує, як їх показати."""

    def __init__(self, user_id, role):
        self.user_id, self.role, self.out = user_id, role, []

    async def reply(self, text, buttons=None):
        self.out.append(Msg(text, buttons))

    async def show(self, rows, buttons=None):
        self.out.append(Msg(rows_text(rows), buttons))


class Store:
    def __init__(self, app, name, fields, unique=()):
        self.app, self.name, self.fields, self.unique = app, name, fields, list(unique)
        cols = ", ".join(f"{q(f)} TEXT" for f in fields)
        app._exec(f"CREATE TABLE IF NOT EXISTS {q(name)} "
                  f"(id INTEGER PRIMARY KEY, {cols}, _hidden INTEGER DEFAULT 0)")
        # автоміграція: нові поля з .bfab додаються до наявної таблиці, дані лишаються
        have = {r[1] for r in app._exec(f"PRAGMA table_info({q(name)})")}
        if "_hidden" not in have:
            app._exec(f"ALTER TABLE {q(name)} ADD COLUMN _hidden INTEGER DEFAULT 0")
        for f in fields:
            if f not in have:
                app._exec(f"ALTER TABLE {q(name)} ADD COLUMN {q(f)} TEXT")
                log.info("міграція: у таблицю %s додано поле %s", name, f)
        self._sync_unique()

    def _sync_unique(self):
        """Унікальність тримається індексом у базі (надійно навіть при одночасних запитах)."""
        prefix = f"ux:{self.name}:"
        old = [r[0] for r in self.app._exec(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ux:%'")]
        for ix in old:
            if ix.startswith(prefix) and ix[len(prefix):] not in self.unique:
                self.app._exec(f"DROP INDEX {q(ix)}")
        for f in self.unique:
            try:
                self.app._exec(f"CREATE UNIQUE INDEX IF NOT EXISTS {q(prefix + f)} "
                               f"ON {q(self.name)} ({q(f)}) WHERE _hidden=0")
            except sqlite3.IntegrityError:
                log.warning("У таблиці %s вже є дублікати в полі %s: індекс не створено, "
                            "унікальність перевіряється лише при додаванні. "
                            "Видали дублікати й перезапусти бота.", self.name, f)

    async def add(self, **v):
        for f in self.unique:
            if f in v and await self.app.run(
                    f"SELECT id FROM {q(self.name)} WHERE _hidden=0 AND {q(f)}=?", [v[f]]):
                raise UserError(f"Такий запис уже є: {f} = {v[f]}")
        ph = ",".join("?" * len(v))
        await self.app.run(
            f"INSERT INTO {q(self.name)} ({','.join(q(c) for c in v)}) VALUES ({ph})", list(v.values()))

    async def search(self, cols, text):
        """Повертає рядки (id, поле1, поле2, ...)."""
        where = " OR ".join(f"{q(c)} LIKE ?" for c in cols)
        return await self.app.run(
            f"SELECT id, {','.join(q(f) for f in self.fields)} FROM {q(self.name)} "
            f"WHERE _hidden=0 AND ({where}) ORDER BY id LIMIT 20",
            [f"%{text}%"] * len(cols))

    async def delete(self, ctx, where, value):
        rows = await self.app.run(
            f"SELECT id FROM {q(self.name)} WHERE _hidden=0 AND {q(where)}=?", [value])
        if not rows:
            raise UserError("Нічого не знайдено.")
        ids = [r[0] for r in rows]
        mark = ",".join("?" * len(ids))
        hide = (f"UPDATE {q(self.name)} SET _hidden=1 WHERE id IN ({mark})", ids)
        show = (f"UPDATE {q(self.name)} SET _hidden=0 WHERE id IN ({mark})", ids)
        await self.app.run(*hide)
        self.app.remember(ctx.user_id, [show], [hide])

    async def change(self, ctx, field, new, where, value):
        rows = await self.app.run(
            f"SELECT id, {q(field)} FROM {q(self.name)} WHERE _hidden=0 AND {q(where)}=?", [value])
        if not rows:
            raise UserError("Нічого не знайдено.")
        sql = f"UPDATE {q(self.name)} SET {q(field)}=? WHERE id=?"
        undo = [(sql, [old, i]) for i, old in rows]
        redo = [(sql, [new, i]) for i, _ in rows]
        await self.app.run_many(redo)  # усе або нічого
        self.app.remember(ctx.user_id, undo, redo)


class Runtime:
    def __init__(self, name, db):
        self.name, self.roles, self.commands = name, {}, {}
        self.stores, self.card_defs = {}, {}
        self.conn = sqlite3.connect(db, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")    # швидший запис, читання не блокує запис
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = asyncio.Lock()
        self._hits = defaultdict(lambda: deque(maxlen=5))
        self._undo = defaultdict(lambda: deque(maxlen=50))  # пам'ятаємо 50 останніх дій
        self._redo = defaultdict(lambda: deque(maxlen=50))
        self.tz = dt.timezone.utc
        self.notifier = None  # адаптер платформи ставить сюди функцію async (user_id, Msg)
        self.scheduler = Scheduler(self)

    def store(self, name, fields, unique=()):
        s = Store(self, name, fields, unique)
        self.stores[name] = s
        return s

    def command(self, name, args, role):
        def deco(fn):
            self.commands[name] = (args, role, fn)
            return fn
        return deco

    # ── час і розклад ──
    def set_timezone(self, name):
        from zoneinfo import ZoneInfo
        self.tz = ZoneInfo(name)

    def now(self):
        return dt.datetime.now(self.tz)

    def system_ctx(self):
        return Ctx(0, "owner")

    def schedule(self, spec):
        def deco(fn):
            self.scheduler.add(spec, fn, fn.__name__)
            return fn
        return deco

    def remind(self, table, field, offsets, at):
        """Щодня о `at` знаходить записи, дата яких (РРРР-ММ-ДД) настане через N днів."""
        def deco(fn):
            store = self.stores[table]

            async def job(ctx):
                today = self.now().date()
                for n in offsets:
                    day = (today + dt.timedelta(days=n)).isoformat()
                    rows = await self.run(
                        f"SELECT id, {','.join(q(f) for f in store.fields)} FROM {q(table)} "
                        f"WHERE _hidden=0 AND {q(field)}=?", [day])
                    for row in rows:
                        rec = dict(zip(["id"] + store.fields, row))
                        rec["days"] = n
                        await fn(ctx, rec)

            self.scheduler.add(("daily", at), job, fn.__name__)
            return fn
        return deco

    async def notify(self, role, text):
        """Надсилає повідомлення всім із роллю не нижче вказаної."""
        if self.notifier is None:
            log.warning("notify: адаптер платформи не підключено")
            return
        for uid, r in list(self.roles.items()):
            if ROLES[r] >= ROLES[role]:
                try:
                    await self.notifier(uid, Msg(text))
                except Exception:
                    log.exception("notify: не вдалось надіслати user=%s", uid)

    def rows_text(self, rows):
        return rows_text(rows)

    # ── база ──
    def _exec(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.fetchall()

    def _exec_many(self, stmts):
        try:
            for sql, params in stmts:
                self.conn.execute(sql, params)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    async def run(self, sql, params=()):
        # база не блокує бота: запит іде в окремому потоці, запити йдуть по черзі
        async with self._lock:
            try:
                return await asyncio.to_thread(self._exec, sql, params)
            except sqlite3.IntegrityError:
                raise UserError("Не вийшло: таке значення вже є (поле має бути унікальним).")

    async def run_many(self, stmts):
        """Кілька запитів в одній транзакції: або всі виконаються, або жоден."""
        async with self._lock:
            try:
                await asyncio.to_thread(self._exec_many, stmts)
            except sqlite3.IntegrityError:
                raise UserError("Не вийшло: таке значення вже є (поле має бути унікальним).")

    def _allowed(self, uid):  # не більше 5 команд за 10 с
        q, now = self._hits[uid], time.monotonic()
        if len(q) == q.maxlen and now - q[0] < 10:
            return False
        q.append(now)
        return True

    def remember(self, uid, undo, redo):
        self._undo[uid].append((undo, redo))
        self._redo[uid].clear()

    async def undo(self, ctx):
        if not self._undo[ctx.user_id]:
            raise UserError("Нічого скасовувати.")
        undo, redo = self._undo[ctx.user_id][-1]
        await self.run_many(undo)  # якщо не вийшло, запис дії лишається в історії
        self._undo[ctx.user_id].pop()
        self._redo[ctx.user_id].append((undo, redo))
        ctx.out.append(Msg("Скасовано."))

    async def redo(self, ctx):
        if not self._redo[ctx.user_id]:
            raise UserError("Нічого повертати.")
        undo, redo = self._redo[ctx.user_id][-1]
        await self.run_many(redo)
        self._redo[ctx.user_id].pop()
        self._undo[ctx.user_id].append((undo, redo))
        ctx.out.append(Msg("Повернуто."))

    async def cards(self, ctx, table, rows):
        """Показує знайдені записи як картки з кнопками (до 5 штук)."""
        card = self.card_defs.get(table)
        if not card or not rows:
            return await ctx.show(rows)
        fields = ["id"] + self.stores[table].fields
        for row in rows[:5]:
            rec = dict(zip(fields, row))
            lines = [f"{f}: {rec[f]}" for f in card["show"]]
            if card["title"]:
                lines.insert(0, str(rec[card["title"]]))
            btns = []
            for label, cmd, fixed in card["buttons"]:
                args = self.commands[cmd][0]
                vals = [str(fixed[a]) if a in fixed else str(rec[a]) for a in args]
                btns.append((label, "/" + " ".join([cmd] + vals)))
            ctx.out.append(Msg("\n".join(lines), [btns[i:i + 2] for i in range(0, len(btns), 2)]))
        if len(rows) > 5:
            ctx.out.append(Msg(f"Показано 5 з {len(rows)}. Уточни пошук."))

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
        return [m if isinstance(m, Msg) else Msg(m) for m in ctx.out]
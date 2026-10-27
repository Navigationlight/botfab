"""Планувальник BotFab: не залежить від Telegram чи іншої платформи."""
import asyncio
import logging
import time

log = logging.getLogger("botfab")

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class Scheduler:
    """Завдання бувають трьох видів (spec):
    ("daily", "18:00")             щодня о 18:00
    ("weekly", "monday", "09:00")  щопонеділка о 09:00
    ("interval", 10)               кожні 10 хвилин
    Час "@назва" означає: беремо його з налаштування кожного користувача окремо
    (особисте завдання: виконується для кожного учасника в його час).
    Час береться з часового поясу бота (app.now()).
    Якщо бот був вимкнений у потрібну хвилину, завдання цього разу пропускається."""

    def __init__(self, app):
        self.app, self.jobs, self.task = app, [], None
        self._running = set()

    def add(self, spec, fn, label, personal=False):
        self.jobs.append({"spec": spec, "fn": fn, "label": label, "personal": personal,
                          "last": {}, "start": time.monotonic()})

    def start(self):
        if self.task is not None or not self.jobs:
            return
        now = time.monotonic()
        for job in self.jobs:
            job["start"] = now
        self.task = asyncio.create_task(self._loop())

    def stop(self):
        if self.task:
            self.task.cancel()
            self.task = None

    def _at(self, job, uid):
        at = job["spec"][-1]
        if isinstance(at, str) and at.startswith("@"):
            return self.app.setting_value(uid, at[1:])
        return at

    def _due(self, job, now, mono, uid, at):
        spec = job["spec"]
        if spec[0] == "interval":
            return mono - job["last"].get(uid, job["start"]) >= spec[1] * 60
        if now.strftime("%H:%M") != at or job["last"].get(uid) == (now.date(), at):
            return False
        return spec[0] == "daily" or WEEKDAYS[now.weekday()] == spec[1]

    def _spawn(self, job, uid=0):
        task = asyncio.create_task(self._run(job, uid))
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        return task

    async def tick(self, now, mono):
        """Одна перевірка: запускає завдання, чий час настав. Повертає запущені задачі."""
        started = []
        for job in self.jobs:
            interval = job["spec"][0] == "interval"
            targets = [uid for uid, _ in self.app.members()] if job["personal"] else [0]
            for uid in targets:
                at = None if interval else self._at(job, uid)
                if self._due(job, now, mono, uid, at):
                    job["last"][uid] = mono if interval else (now.date(), at)
                    started.append(self._spawn(job, uid))
        return started

    async def _loop(self):
        while True:
            try:
                await self.tick(self.app.now(), time.monotonic())
            except Exception:
                log.exception("збій планувальника")
            await asyncio.sleep(10)

    async def run_all(self):
        """Виконати всі завдання зараз (для перевірки в консолі)."""
        tasks = []
        for job in self.jobs:
            if job["personal"]:
                tasks += [self._spawn(job, uid) for uid, _ in self.app.members()]
            else:
                tasks.append(self._spawn(job, 0))
        await asyncio.gather(*tasks)

    async def _run(self, job, uid=0):
        ctx = self.app.user_ctx(uid)
        try:
            await asyncio.wait_for(job["fn"](ctx), timeout=30)
        except Exception:
            log.exception("збій завдання %s", job["label"])
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
    Час береться з часового поясу бота (app.now()).
    Якщо бот був вимкнений у потрібну хвилину, завдання цього разу пропускається."""

    def __init__(self, app):
        self.app, self.jobs, self.task = app, [], None
        self._running = set()

    def add(self, spec, fn, label):
        self.jobs.append({"spec": spec, "fn": fn, "label": label, "last": None})

    def start(self):
        if self.task is not None or not self.jobs:
            return
        now = time.monotonic()
        for job in self.jobs:
            if job["spec"][0] == "interval":
                job["last"] = now
        self.task = asyncio.create_task(self._loop())

    def stop(self):
        if self.task:
            self.task.cancel()
            self.task = None

    def _due(self, job, now, mono):
        spec = job["spec"]
        if spec[0] == "interval":
            return mono - job["last"] >= spec[1] * 60
        at = spec[-1]
        if now.strftime("%H:%M") != at or job["last"] == (now.date(), at):
            return False
        return spec[0] == "daily" or WEEKDAYS[now.weekday()] == spec[1]

    def _spawn(self, job):
        task = asyncio.create_task(self._run(job))
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        return task

    async def tick(self, now, mono):
        """Одна перевірка: запускає завдання, чий час настав. Повертає запущені задачі."""
        started = []
        for job in self.jobs:
            if self._due(job, now, mono):
                job["last"] = mono if job["spec"][0] == "interval" else (now.date(), job["spec"][-1])
                started.append(self._spawn(job))
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
        await asyncio.gather(*[self._spawn(j) for j in self.jobs])

    async def _run(self, job):
        ctx = self.app.system_ctx()
        try:
            await asyncio.wait_for(job["fn"](ctx), timeout=30)
        except Exception:
            log.exception("збій завдання %s", job["label"])
import asyncio
import datetime as dt

from botfab.compiler import check
from botfab.runtime import Runtime


PLANNER = (
    'timezone "Europe/Kyiv"\n'
    'store tasks:\n    title, due\n\n'
    'roles:\n    owner 2\n\n'
    'command "task" with due title:\n'
    '    add tasks with title = title, due = due\n'
    '    reply "ok"\n\n'
    'remind tasks by due before 3, 1 days at 18:00:\n'
    '    notify member "⏰ {title}: залишилось днів {days} (до {due})"\n\n'
    'every day at 09:00:\n'
    '    find tasks where title contains ""\n'
    '    notify member found\n'
)


def build(src=PLANNER):
    """Тести будують бота зі свого тексту і не залежать від файлів у examples/."""
    py, errors = check(src)
    assert errors == [], errors
    ns = {"Runtime": Runtime}
    exec(py, ns)
    return ns["app"]


def test_examples_compile():
    for name in ("archive", "planner"):
        _, errors = check(open(f"examples/{name}.bfab", encoding="utf-8").read())
        assert errors == [], (name, errors)


def test_planner_compiles():
    app = build()
    assert len(app.scheduler.jobs) == 2


def test_bad_time_and_day_give_hints():
    for head, word in [("every day at 25:00:", "час"), ("every mondey at 09:00:", "monday"),
                       ("every 0 minutes:", "хвилини")]:
        _, errors = check(f'store t:\n    a\n\n{head}\n    find t where a contains "x"\n')
        assert errors and word in errors[0], (head, errors)


def test_reply_not_allowed_in_schedule():
    _, errors = check('store t:\n    a\n\nevery day at 09:00:\n    reply "hi"\n')
    assert "розкладі" in errors[0]


def test_bad_timezone():
    _, errors = check('timezone "Europe/Kiyv"\n')
    assert "часовий пояс" in errors[0]


def test_remind_and_daily_fire():
    async def scenario():
        app = build()
        sent = []

        async def notifier(uid, msg):
            sent.append((uid, msg.text))

        app.notifier = notifier
        today = app.now().date()
        tomorrow = (today + dt.timedelta(days=1)).isoformat()
        later = (today + dt.timedelta(days=30)).isoformat()
        await app.handle(2, f"/task {tomorrow} Здати лабу")
        await app.handle(2, f"/task {later} Далека задача")
        base = app.now().replace(second=0, microsecond=0)

        # 18:00 -> нагадування лише про задачу на завтра
        tasks = await app.scheduler.tick(base.replace(hour=18, minute=0), 1000.0)
        await asyncio.gather(*tasks)
        assert any("Здати лабу" in t and "залишилось днів 1" in t for _, t in sent)
        assert not any("Далека задача" in t for _, t in sent)
        # цієї ж хвилини повторно не спрацьовує
        assert await app.scheduler.tick(base.replace(hour=18, minute=0), 1001.0) == []

        # 09:00 -> список усіх задач
        sent.clear()
        tasks = await app.scheduler.tick(base.replace(hour=9, minute=0), 1002.0)
        await asyncio.gather(*tasks)
        assert any("Далека задача" in t for _, t in sent)

        # о 12:00 нічого не відбувається
        assert await app.scheduler.tick(base.replace(hour=12, minute=0), 1003.0) == []

    asyncio.run(scenario())


def test_interval_and_weekly():
    src = ('store t:\n    a\n\nroles:\n    owner 1\n\n'
           'every 5 minutes:\n    notify owner "ping"\n\n'
           'every monday at 08:00:\n    notify owner "week"\n')
    py, errors = check(src)
    assert errors == []

    async def scenario():
        ns = {"Runtime": Runtime}
        exec(py, ns)
        app = ns["app"]
        sent = []

        async def notifier(uid, msg):
            sent.append(msg.text)

        app.notifier = notifier
        app.scheduler.start()  # задає старт відліку для interval
        for j in app.scheduler.jobs:
            if j["spec"][0] == "interval":
                j["last"] = 0.0
        monday = dt.datetime(2026, 10, 26, 8, 0, tzinfo=dt.timezone.utc)  # понеділок
        tuesday = dt.datetime(2026, 10, 27, 8, 0, tzinfo=dt.timezone.utc)
        app.scheduler.stop()
        t = await app.scheduler.tick(monday, 400.0)   # 400 c > 5 хв -> ping, і week
        await asyncio.gather(*t)
        assert sorted(sent) == ["ping", "week"]
        sent.clear()
        t = await app.scheduler.tick(tuesday, 450.0)  # 50 c від попереднього -> нічого
        assert t == []

    asyncio.run(scenario())
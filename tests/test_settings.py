import asyncio
import datetime as dt

from botfab.compiler import check
from botfab.runtime import Runtime

BASE = (
    'timezone "Europe/Kyiv"\n'
    'store tasks:\n    title, due\n\n'
    'roles:\n    owner 2\n    member 3\n\n'
    'setting remind_at: time = 18:00\n'
    'setting remind_days: days = 3, 1\n'
    'setting greeting: text = "Привіт"\n\n'
    'command "task" with due title:\n'
    '    add tasks with title = title, due = due\n'
    '    reply "{setting.greeting}! Збережено {title}"\n\n'
    'remind tasks by due before setting.remind_days days at setting.remind_at:\n'
    '    notify me "{setting.greeting}! {title}: ще {days} дн."\n'
)


def build(src=BASE):
    py, errors = check(src)
    assert errors == [], errors
    ns = {"Runtime": Runtime}
    exec(py, ns)
    return ns["app"]


def say(app, text, uid=2):
    app._hits.clear()
    return [m.text for m in asyncio.run(app.handle(uid, text))]


async def asay(app, text, uid=2):
    app._hits.clear()
    return [m.text for m in await app.handle(uid, text)]


def errors_of(src):
    _, e = check(src)
    return "\n".join(e)


def test_settings_command_lists_values_and_defaults():
    app = build()
    out = say(app, "/settings")[0]
    assert "remind_at = 18:00" in out and "remind_days = 3, 1" in out and "greeting = Привіт" in out


def test_set_validates_and_saves():
    app = build()
    assert say(app, "/set remind_at 07:30") == ["Збережено: remind_at = 07:30"]
    assert say(app, "/set remind_at 7:5")[0].startswith("Час пиши так")
    assert say(app, "/set remind_at 25:00")[0].startswith("Час пиши так")
    assert "Немає налаштування" in say(app, "/set nothing 1")[0]
    assert say(app, "/set remind_days 5,  2 ,5") == ["Збережено: remind_days = 5, 2"]
    assert "remind_at = 07:30" in say(app, "/settings")[0]
    assert "remind_at = 18:00" in say(app, "/settings", uid=3)[0]  # у іншого користувача своє


def test_settings_survive_restart(tmp_path):
    src = f'database sqlite "{tmp_path / "s.db"}"\n' + BASE
    app1 = build(src)
    say(app1, "/set greeting Вітаю")
    app1.conn.close()
    app2 = build(src)
    assert "greeting = Вітаю" in say(app2, "/settings")[0]


def test_reply_uses_user_setting():
    app = build()
    say(app, "/set greeting Hello", uid=2)
    assert say(app, "/task 2026-12-01 A", uid=2) == ["Hello! Збережено A"]
    assert say(app, "/task 2026-12-02 B", uid=3) == ["Привіт! Збережено B"]


def test_personal_remind_each_user_own_time_days_and_text():
    async def scenario():
        app = build()
        sent = []

        async def notifier(uid, msg):
            sent.append((uid, msg.text))

        app.notifier = notifier
        await asay(app, "/set remind_at 07:30", uid=2)
        await asay(app, "/set greeting Hello", uid=2)
        await asay(app, "/set remind_days 5", uid=3)
        today = app.now().date()
        d1 = (today + dt.timedelta(days=1)).isoformat()
        d5 = (today + dt.timedelta(days=5)).isoformat()
        await asay(app, f"/task {d1} Завтра", uid=2)
        await asay(app, f"/task {d5} ЧерезП", uid=2)
        base = app.now().replace(second=0, microsecond=0)

        tasks = await app.scheduler.tick(base.replace(hour=7, minute=30), 1.0)
        await asyncio.gather(*tasks)
        assert sent == [(2, "Hello! Завтра: ще 1 дн.")]  # у 2 дні 3,1; її час 07:30

        sent.clear()
        assert await app.scheduler.tick(base.replace(hour=7, minute=30), 2.0) == []  # не повторюється
        tasks = await app.scheduler.tick(base.replace(hour=18, minute=0), 3.0)
        await asyncio.gather(*tasks)
        assert sent == [(3, "Привіт! ЧерезП: ще 5 дн.")]  # у 3 час 18:00, дні лише 5

    asyncio.run(scenario())


def test_every_day_at_setting_notify_me_found():
    src = ('store t:\n    a\n\nroles:\n    owner 2\n    member 3\n\n'
           'setting digest_at: time = 09:00\n\n'
           'command "add" with a:\n    add t with a = a\n    reply "ok"\n\n'
           'every day at setting.digest_at:\n    find t where a contains ""\n    notify me found\n')

    async def scenario():
        app = build(src)
        sent = []

        async def notifier(uid, msg):
            sent.append((uid, msg.text))

        app.notifier = notifier
        await asay(app, "/set digest_at 10:15", uid=3)
        await asay(app, "/add {x}")  # фігурні дужки в даних не ламають відправку
        base = app.now().replace(second=0, microsecond=0)
        await asyncio.gather(*await app.scheduler.tick(base.replace(hour=9, minute=0), 1.0))
        await asyncio.gather(*await app.scheduler.tick(base.replace(hour=10, minute=15), 2.0))
        assert sorted(u for u, _ in sent) == [2, 3]
        assert all("{x}" in t for _, t in sent)

    asyncio.run(scenario())


def test_compile_errors_are_helpful():
    assert "не існує" not in errors_of(BASE)
    e = errors_of(BASE.replace("{setting.greeting}! Збережено", "{setting.greting}! Збережено"))
    assert "greeting" in e                                             # підказка з назвою
    assert "некоректне значення" in errors_of("setting t: time = 25:00\n")
    assert "лапках" in errors_of("setting g: text = hello\n")
    assert "уже оголошене" in errors_of("setting a: number = 1\nsetting a: number = 2\n")
    assert "тип" in errors_of(BASE.replace("at setting.remind_at", "at setting.greeting"))
    assert "тип" in errors_of(BASE.replace("before setting.remind_days", "before setting.remind_at"))
    assert "notify me" in errors_of(BASE.replace("notify me", "notify member"))
    glob = ('store t:\n    a\n\nevery day at 09:00:\n    find t where a contains ""\n    notify me found\n')
    assert "лише в розкладі" in errors_of(glob)
    assert "автоматично" in errors_of('setting a: number = 1\ncommand "set":\n    reply "x"\n')
    assert "зарезервоване" in errors_of("store t:\n    setting\n")
    assert "setting" in errors_of('command "c" with setting:\n    reply "x"\n')
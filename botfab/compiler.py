import re
from zoneinfo import ZoneInfo

from .common import ROLES, CompileError, suggest
from .scheduler import WEEKDAYS
from .settings import parse_value

TOP = ["bot", "database", "timezone", "setting", "store", "roles", "command", "card", "every", "remind"]
STATEMENTS = ["only", "find", "show", "add", "reply", "delete", "rename", "move",
              "undo", "redo", "notify"]
JOB_STATEMENTS = ["find", "add", "notify"]  # що дозволено в розкладі (every)
EVERY_HINT = "форми: every day at 18:00 | every monday at 09:00 | every 10 minutes"


def split_blocks(src):
    """Ділить текст на блоки. Повертає (блоки, помилки)."""
    blocks, errors, cur = [], [], None
    for n, line in enumerate(src.splitlines(), 1):
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line[0] not in " \t":
            cur = (n, line.strip(), [])
            blocks.append(cur)
        elif cur is None:
            errors.append(CompileError(n, "відступ без заголовка зверху").text)
        else:
            cur[2].append((n, line.strip()))
    return blocks, errors


def parse_time(s, n):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise CompileError(n, f"некоректний час «{s}»", "формат: 18:00 (години 0-23, хвилини 00-59)")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_at(n, tok, settings):
    """Час: 18:00 або setting.назва (час із налаштувань кожного користувача)."""
    m = re.fullmatch(r"setting\.(\w+)", tok)
    if not m:
        return parse_time(tok, n)
    name = m.group(1)
    if name not in settings:
        raise CompileError(n, f"немає налаштування «{name}»",
                           suggest(name, settings) or "оголоси: setting remind_at: time = 18:00")
    if settings[name][0] != "time":
        raise CompileError(n, f"налаштування «{name}» має тип {settings[name][0]}, а тут потрібен time")
    return "@" + name


def check_ph(text, allowed, settings, ln):
    """Перевіряє {поля} і {setting.назва} у тексті."""
    for a, b in re.findall(r"\{(\w+)(?:\.(\w+))?\}", text):
        if b:
            if a != "setting":
                raise CompileError(ln, f"у тексті {{{a}.{b}}}: крапка працює лише з setting",
                                   "приклад: {setting.greeting}")
            if b not in settings:
                raise CompileError(ln, f"немає налаштування «{b}»",
                                   suggest(b, settings) or "оголоси: setting greeting: text = \"Привіт\"")
        elif a not in allowed:
            raise CompileError(ln, f"у тексті {{{a}}}, але такого поля чи аргументу немає",
                               suggest(a, allowed))


def parse_every(n, head, settings):
    m = re.fullmatch(r"every (.*):", head)
    if not m:
        raise CompileError(n, "не зрозумів every (в кінці рядка має бути «:»)", EVERY_HINT)
    spec = m.group(1).strip()
    m = re.fullmatch(r"day at (\S+)", spec)
    if m:
        return ("daily", parse_at(n, m.group(1), settings))
    m = re.fullmatch(r"(\w+) at (\S+)", spec)
    if m:
        day = m.group(1)
        if day not in WEEKDAYS:
            raise CompileError(n, f"невідомий день «{day}»",
                               suggest(day, WEEKDAYS) or "monday, tuesday ... sunday або day")
        return ("weekly", day, parse_at(n, m.group(2), settings))
    m = re.fullmatch(r"(?:(\d+) )?minutes?", spec)
    if m:
        k = int(m.group(1) or 1)
        if k < 1:
            raise CompileError(n, "інтервал має бути від 1 хвилини")
        return ("interval", k)
    raise CompileError(n, "не зрозумів every", EVERY_HINT)


def compile_command(n, name, args, body, stores, spec=None, settings=None):
    """Повертає (код, помилки). Збирає помилки з усіх рядків команди.
    Якщо задано spec, це завдання за розкладом (every), а не команда."""
    is_job = spec is not None
    settings = settings or {}
    personal = is_job and spec[0] != "interval" and str(spec[-1]).startswith("@")
    role, out, found, found_table = "member", [], False, None  # за замовчуванням закрито для гостей
    errors = []

    def val(tok, ln):
        tok = tok.strip()
        if re.fullmatch(r'"[^"]*"', tok):
            return repr(tok[1:-1])
        if tok in args:
            return tok
        raise CompileError(ln, f"невідоме значення «{tok}»",
                           suggest(tok, args) or "текст пиши в лапках")

    def table(t, ln):
        if t not in stores:
            raise CompileError(ln, f"немає таблиці «{t}»", suggest(t, stores))
        return stores[t]

    def fields(fs, allowed, ln):
        for f in fs:
            if f not in allowed:
                raise CompileError(ln, f"у таблиці немає поля «{f}»", suggest(f, allowed))

    if not body:
        err = CompileError(n, f"«{name}» порожнє", "додай хоча б один рядок, наприклад reply \"...\"")
        return "", [err.text]

    for ln, s in body:
        try:
            kw = s.split()[0]
            if is_job and kw in STATEMENTS and kw not in JOB_STATEMENTS:
                raise CompileError(ln, f"«{kw}» не працює в розкладі",
                                   "тут можна: " + ", ".join(JOB_STATEMENTS))
            if kw == "only":
                m = re.fullmatch(r"only for (\w+)", s)
                who = m.group(1) if m else None
                if who == "everyone":
                    who = "guest"
                if who not in ROLES:
                    raise CompileError(ln, "ролі немає", "є: everyone, member, owner")
                role = who
            elif kw == "find":
                m = re.fullmatch(r'find (\w+) where (\w+(?: or \w+)*) contains ("[^"]*"|\w+)', s)
                if not m:
                    raise CompileError(ln, "не зрозумів find",
                                       "форма: find files where name or tag contains text")
                t, fs, a = m.group(1), m.group(2).split(" or "), m.group(3)
                fields(fs, table(t, ln), ln)
                v = val(a, ln)
                out.append(f"found = await {t}.search({fs!r}, {v})")
                found, found_table = True, t
            elif kw == "show":
                m = re.fullmatch(r"show found( as cards)?", s)
                if not m or not found:
                    raise CompileError(ln, "нема що показувати",
                                       "спочатку find, потім: show found  або  show found as cards")
                if m.group(1):
                    out.append(f"await app.cards(ctx, {found_table!r}, found)")
                else:
                    out.append("await ctx.show(found)")
            elif kw == "add":
                m = re.fullmatch(r"add (\w+) with (.+)", s)
                if not m:
                    raise CompileError(ln, "не зрозумів add",
                                       'форма: add files with name = name, tag = "x"')
                t = m.group(1)
                pairs = [p.split("=", 1) for p in m.group(2).split(",")]
                if any(len(p) != 2 for p in pairs):
                    raise CompileError(ln, "у add кожне поле має вигляд поле = значення")
                fields([p[0].strip() for p in pairs], table(t, ln), ln)
                kv = ", ".join(f"{p[0].strip()}={val(p[1], ln)}" for p in pairs)
                out.append(f"await {t}.add({kv})")
            elif kw == "reply":
                m = re.fullmatch(r'reply "(.*)"', s)
                if not m:
                    raise CompileError(ln, "reply потребує текст у лапках")
                check_ph(m.group(1), args, settings, ln)
                parts = [f"{a}={a}" for a in args] + ["setting=app.settings(ctx.user_id)"]
                out.append(f"await ctx.reply({m.group(1)!r}.format({', '.join(parts)}))")
            elif kw == "notify":
                m = re.fullmatch(r'notify (member|owner|me) (found|"(.*)")', s)
                if not m:
                    raise CompileError(ln, "не зрозумів notify",
                                       'форми: notify member "текст"  |  notify me "текст"  |  notify owner found')
                who, what, text = m.groups()
                if who == "me" and is_job and not personal:
                    raise CompileError(ln, "notify me працює лише в розкладі з часом із налаштувань",
                                       "наприклад: every day at setting.digest_at:")
                if who != "me" and personal:
                    raise CompileError(ln, "в особистому розкладі кожен отримує своє",
                                       "пиши notify me замість notify " + who)
                kw_args = "".join(f", {a}={a}" for a in args)
                if what == "found":
                    if not found:
                        raise CompileError(ln, "нема що надсилати", "спочатку find, потім: notify member found")
                    call = "app.notify_user(ctx.user_id" if who == "me" else f"app.notify({who!r}"
                    out.append("if found:")
                    out.append(f'    await {call}, "{{rows}}", rows=app.rows_text(found))')
                else:
                    check_ph(text, args, settings, ln)
                    call = "app.notify_user(ctx.user_id" if who == "me" else f"app.notify({who!r}"
                    out.append(f"await {call}, {text!r}{kw_args})")
            elif kw == "delete":
                m = re.fullmatch(r'delete (\w+) where (\w+) is ("[^"]*"|\w+)', s)
                if not m:
                    raise CompileError(ln, "не зрозумів delete",
                                       "форма: delete files where name is name")
                t, w, a = m.groups()
                fields([w], table(t, ln) + ["id"], ln)
                out.append(f"await {t}.delete(ctx, {w!r}, {val(a, ln)})")
            elif kw in ("rename", "move"):
                m = re.fullmatch(rf'{kw} (\w+) where (\w+) is ("[^"]*"|\w+) to ("[^"]*"|\w+)', s)
                if not m:
                    ex = "new_name" if kw == "rename" else '"work"'
                    raise CompileError(ln, f"не зрозумів {kw}",
                                       f"форма: {kw} files where name is name to {ex}")
                t, w, a, b = m.groups()
                field = "name" if kw == "rename" else "folder"
                fields([w], table(t, ln) + ["id"], ln)
                fields([field], table(t, ln), ln)
                out.append(f"await {t}.change(ctx, {field!r}, {val(b, ln)}, {w!r}, {val(a, ln)})")
            elif kw in ("undo", "redo"):
                if s != kw:
                    raise CompileError(ln, f"після {kw} нічого не пиши")
                out.append(f"await app.{kw}(ctx)")
            else:
                raise CompileError(ln, f"невідома дія «{kw}»", suggest(kw, STATEMENTS))
        except CompileError as e:
            errors.append(e.text)  # запам'ятали помилку і йдемо до наступного рядка

    if errors:
        return "", errors
    if is_job:
        code = [f"@app.schedule({spec!r})", f"async def job_{name}(ctx):"]
    else:
        code = [f'@app.command("{name}", {args!r}, "{role}")',
                f"async def cmd_{name}(ctx{''.join(', ' + a for a in args)}):"]
    code += ["    " + c for c in out]
    return "\n".join(code), []


def compile_remind(n, head, body, stores, settings):
    """remind tasks by due before 5, 3, 1 days at 18:00:  ->  (код, помилки).
    Дні й час можна брати з налаштувань: before setting.remind_days days at setting.remind_at"""
    m = re.fullmatch(r"remind (\w+) by (\w+) before (setting\.\w+|\d+(?:\s*,\s*\d+)*) days?"
                     r"(?: at (\S+?))?:", head)
    if not m:
        return "", [CompileError(n, "не зрозумів remind",
                                 "форма: remind tasks by due before 3, 1 days at 18:00:").text]
    t, field, offs, at = m.groups()
    try:
        at = parse_at(n, at or "09:00", settings)
        if offs.startswith("setting."):
            name = offs[8:]
            if name not in settings:
                raise CompileError(n, f"немає налаштування «{name}»",
                                   suggest(name, settings) or "оголоси: setting remind_days: days = 3, 1")
            if settings[name][0] != "days":
                raise CompileError(n, f"налаштування «{name}» має тип {settings[name][0]}, а тут потрібен days")
            offsets = "@" + name
        else:
            offsets = [int(x) for x in re.split(r"\s*,\s*", offs)]
        if t not in stores:
            raise CompileError(n, f"немає таблиці «{t}»", suggest(t, stores))
        if field not in stores[t]:
            raise CompileError(n, f"у таблиці немає поля «{field}»", suggest(field, stores[t]))
    except CompileError as e:
        return "", [e.text]
    personal = isinstance(offsets, str) or at.startswith("@")
    allowed = ["id"] + list(stores[t]) + ["days"]
    if not body:
        return "", [CompileError(n, "remind порожній", 'додай: notify member "{title} через {days} дн."').text]
    out, errors = [], []
    for ln, s in body:
        m = re.fullmatch(r'notify (member|owner|me) "(.*)"', s)
        if not m:
            errors.append(CompileError(ln, "у remind можна лише notify",
                                       'форма: notify member "текст з {полями} і {days}"').text)
            continue
        who, text = m.groups()
        try:
            if who == "me" and not personal:
                raise CompileError(ln, "notify me працює, коли дні або час беруться з налаштувань",
                                   "додай: before setting.remind_days days at setting.remind_at")
            if who != "me" and personal:
                raise CompileError(ln, "тут кожен отримує своє", "пиши notify me замість notify " + who)
            check_ph(text, allowed, settings, ln)
        except CompileError as e:
            errors.append(e.text)
            continue
        call = "app.notify_user(ctx.user_id" if who == "me" else f"app.notify({who!r}"
        out.append(f"await {call}, {text!r}, **rec)")
    if errors:
        return "", errors
    code = [f"@app.remind({t!r}, {field!r}, {offsets!r}, {at!r})", f"async def remind_{n}(ctx, rec):"]
    code += ["    " + c for c in out]
    return "\n".join(code), []


def compile_card(n, table, body, stores, cmd_args):
    """Картка таблиці: заголовок, поля, кнопки. Повертає (опис, помилки)."""
    if table not in stores:
        return None, [CompileError(n, f"немає таблиці «{table}»", suggest(table, stores)).text]
    allowed = ["id"] + stores[table]
    card, errors = {"title": None, "show": [], "buttons": []}, []
    for ln, s in body:
        try:
            kw = s.split()[0]
            if kw == "title":
                m = re.fullmatch(r"title (\w+)", s)
                if not m or m.group(1) not in allowed:
                    raise CompileError(ln, "title: вкажи поле таблиці", "поля: " + ", ".join(allowed))
                card["title"] = m.group(1)
            elif kw == "show":
                fs = [f.strip() for f in s[4:].split(",") if f.strip()]
                for f in fs:
                    if f not in allowed:
                        raise CompileError(ln, f"у таблиці немає поля «{f}»", suggest(f, allowed))
                card["show"] = fs
            elif kw == "button":
                m = re.fullmatch(r'button "([^"]+)" runs (\w+)(?: with (.+))?', s)
                if not m:
                    raise CompileError(ln, "не зрозумів button", 'форма: button "🗑 Delete" runs remove')
                label, cmd, extra = m.groups()
                if cmd not in cmd_args:
                    raise CompileError(ln, f"немає команди «{cmd}»", suggest(cmd, cmd_args))
                fixed = {}
                for pair in (extra or "").split(","):
                    if not pair.strip():
                        continue
                    k, _, v = pair.partition("=")
                    k, v = k.strip(), v.strip()
                    if k not in cmd_args[cmd]:
                        raise CompileError(ln, f"у команди «{cmd}» немає аргументу «{k}»",
                                           "аргументи: " + (", ".join(cmd_args[cmd]) or "немає"))
                    if not re.fullmatch(r'"[^"]*"', v):
                        raise CompileError(ln, "значення після = пиши в лапках")
                    fixed[k] = v[1:-1]
                missing = [a for a in cmd_args[cmd] if a not in fixed and a not in allowed]
                if missing:
                    raise CompileError(ln, f"кнопка не знає, що підставити в «{missing[0]}»",
                                       f'додай: with {missing[0]} = "..."')
                card["buttons"].append((label, cmd, fixed))
            else:
                raise CompileError(ln, f"невідоме слово «{kw}»",
                                   suggest(kw, ["title", "show", "button"]))
        except CompileError as e:
            errors.append(e.text)
    return card, errors


def compile_bot(src):
    """Повертає (python-код або None, список усіх помилок)."""
    name, db, tz, stores, roles = "Bot", ":memory:", None, {}, {}
    uniques, settings, raw_jobs = {}, {}, []
    cmds, cards, jobs, reminds = [], {}, [], []
    blocks, errors = split_blocks(src)
    for n, head, body in blocks:
        try:
            kw = head.split()[0].rstrip(":")
            if kw == "bot":
                m = re.fullmatch(r'bot "(.+)"', head)
                if not m:
                    raise CompileError(n, 'назву бота пиши так: bot "Назва"')
                name = m.group(1)
            elif kw == "database":
                m = re.fullmatch(r'database (\w+) "(.+)"', head)
                if not m:
                    raise CompileError(n, 'форма: database sqlite "bot.db"')
                if m.group(1) != "sqlite":
                    raise CompileError(n, f"база «{m.group(1)}» ще не підтримується",
                                       "зараз є sqlite")
                db = m.group(2)
            elif kw == "timezone":
                m = re.fullmatch(r'timezone "(.+)"', head)
                if not m:
                    raise CompileError(n, 'форма: timezone "Europe/Kyiv"')
                try:
                    ZoneInfo(m.group(1))
                except Exception:
                    raise CompileError(n, f"невідомий часовий пояс «{m.group(1)}»",
                                       "приклад: Europe/Kyiv. Якщо назва правильна, виконай: pip install tzdata")
                tz = m.group(1)
            elif kw == "setting":
                m = re.fullmatch(r"setting ([a-z][a-z0-9_]*): (time|days|number|text) = (.+)", head)
                if not m:
                    raise CompileError(n, "не зрозумів setting",
                                       'форма: setting remind_at: time = 18:00  (типи: time, days, number, text)')
                key, kind, default = m.groups()
                if kind == "text":
                    q = re.fullmatch(r'"(.*)"', default.strip())
                    if not q:
                        raise CompileError(n, "текст за замовчуванням пиши в лапках",
                                           'setting greeting: text = "Привіт"')
                    default = q.group(1)
                try:
                    default = parse_value(kind, default)
                except ValueError as e:
                    raise CompileError(n, f"некоректне значення за замовчуванням: {e}")
                if key in settings:
                    raise CompileError(n, f"налаштування «{key}» уже оголошене")
                settings[key] = (kind, default)
            elif kw == "store":
                m = re.fullmatch(r"store ([a-z_]\w*):", head)
                if not m or not body:
                    raise CompileError(n, "форма: store files: і нижче поля через кому")
                fs, uniq = [], []
                for ln, l in body:
                    for tok in l.split(","):
                        words = tok.split()
                        if not words:
                            continue
                        fname, mods = words[0], words[1:]
                        if not re.fullmatch(r"[a-z][a-z0-9_]*", fname):
                            errors.append(CompileError(
                                ln, f"некоректна назва поля «{fname}»",
                                "латинські малі літери, цифри і _; починай з літери").text)
                            continue
                        if fname in ("id", "setting") or fname in fs:
                            why = {"id": "зарезервоване (id є в кожної таблиці)",
                                   "setting": "зарезервоване (так звертаються до налаштувань)"}
                            errors.append(CompileError(
                                ln, f"поле «{fname}» " + why.get(fname, "вже є")).text)
                            continue
                        for mod in mods:
                            if mod == "unique":
                                uniq.append(fname)
                            else:
                                errors.append(CompileError(
                                    ln, f"невідома властивість «{mod}»",
                                    suggest(mod, ["unique"]) or "є: unique").text)
                        fs.append(fname)
                stores[m.group(1)] = fs
                uniques[m.group(1)] = uniq
            elif kw == "roles":
                for ln, l in body:
                    m = re.fullmatch(r"(owner|member) (\d+)", l)
                    if not m:
                        errors.append(CompileError(ln, "форма: owner 12345",
                                                   "ролі: owner, member").text)
                        continue
                    roles[int(m.group(2))] = m.group(1)
            elif kw == "command":
                m = re.fullmatch(r'command "([a-z_]\w*)"(?: with ([a-z_ ]+))?:', head)
                if not m:
                    raise CompileError(n, 'форма: command "find" with text:')
                cargs = (m.group(2) or "").split()
                if "setting" in cargs:
                    raise CompileError(n, "аргумент не може називатися «setting»", "обери іншу назву")
                cmds.append((n, m.group(1), cargs, body))
            elif kw == "card":
                m = re.fullmatch(r"card ([a-z_]\w*):", head)
                if not m or not body:
                    raise CompileError(n, "форма: card files: і нижче title, show, button")
                cards[m.group(1)] = (n, body)
            elif kw == "every":
                raw_jobs.append((n, head, body))
            elif kw == "remind":
                reminds.append((n, head, body))
            else:
                raise CompileError(n, f"невідоме слово «{kw}»", suggest(kw, TOP))
        except CompileError as e:
            errors.append(e.text)

    for n, head, body in raw_jobs:
        try:
            jobs.append((n, parse_every(n, head, settings), body))
        except CompileError as e:
            errors.append(e.text)
    if settings:
        for n, cname, _, _ in cmds:
            if cname in ("settings", "set"):
                errors.append(CompileError(n, f"команда «{cname}» створюється автоматично, коли є налаштування",
                                           "обери іншу назву").text)

    code = ["# згенеровано BotFab", f"app = Runtime({name!r}, {db!r})",
            f"app.roles.update({roles!r})"]
    if tz:
        code.append(f"app.set_timezone({tz!r})")
    for key, (kind, default) in settings.items():
        code.append(f"app.define_setting({key!r}, {kind!r}, {default!r})")
    for t, fs in stores.items():
        code.append(f"{t} = app.store({t!r}, {fs!r}, {uniques[t]!r})")
    # команди, картки й розклад компілюємо в кінці, коли вже відомі всі таблиці
    for n, cname, args, body in cmds:
        cmd_code, cmd_errors = compile_command(n, cname, args, body, stores, settings=settings)
        errors += cmd_errors
        code.append("")
        code.append(cmd_code)
    cmd_args = {c[1]: c[2] for c in cmds}
    for t, (n, body) in cards.items():
        card, card_errors = compile_card(n, t, body, stores, cmd_args)
        errors += card_errors
        if card and not card_errors:
            code.append("")
            code.append(f"app.card_defs[{t!r}] = {card!r}")
    for n, spec, body in jobs:
        job_code, job_errors = compile_command(n, str(n), [], body, stores, spec=spec, settings=settings)
        errors += job_errors
        code.append("")
        code.append(job_code)
    for n, head, body in reminds:
        r_code, r_errors = compile_remind(n, head, body, stores, settings)
        errors += r_errors
        code.append("")
        code.append(r_code)

    if errors:
        return None, errors
    return "\n".join(code) + "\n", []


def check(src):
    return compile_bot(src)
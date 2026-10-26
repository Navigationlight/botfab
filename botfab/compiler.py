import re

from .common import ROLES, CompileError, suggest

TOP = ["bot", "database", "store", "roles", "command", "card"]
STATEMENTS = ["only", "find", "show", "add", "reply", "delete", "rename", "move", "undo", "redo"]


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


def compile_command(n, name, args, body, stores):
    """Повертає (код, помилки). Збирає помилки з усіх рядків команди."""
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
        err = CompileError(n, f"команда «{name}» порожня", "додай хоча б reply \"...\"")
        return "", [err.text]

    for ln, s in body:
        try:
            kw = s.split()[0]
            if kw == "only":
                m = re.fullmatch(r"only for (\w+)", s)
                who = m.group(1) if m else None
                if who == "everyone":
                    who = "guest"
                if who not in ROLES:
                    raise CompileError(ln, "ролі немає", "є: everyone, member, owner")
                role = who
            elif kw == "find":
                m = re.fullmatch(r"find (\w+) where (\w+(?: or \w+)*) contains (\w+)", s)
                if not m:
                    raise CompileError(ln, "не зрозумів find",
                                       "форма: find files where name or tag contains text")
                t, fs, a = m.group(1), m.group(2).split(" or "), m.group(3)
                fields(fs, table(t, ln), ln)
                val(a, ln)
                out.append(f"found = await {t}.search({fs!r}, {a})")
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
                for ph in re.findall(r"\{(\w+)\}", m.group(1)):
                    if ph not in args:
                        raise CompileError(ln, f"у тексті {{{ph}}}, але такого аргументу немає",
                                           suggest(ph, args))
                fmt = ", ".join(f"{a}={a}" for a in args)
                out.append(f"await ctx.reply({m.group(1)!r}.format({fmt}))")
            elif kw == "delete":
                m = re.fullmatch(r'delete (\w+) where (\w+) is ("[^"]*"|\w+)', s)
                if not m:
                    raise CompileError(ln, "не зрозумів delete",
                                       "форма: delete files where name is name")
                t, w, a = m.groups()
                fields([w], table(t, ln), ln)
                out.append(f"await {t}.delete(ctx, {w!r}, {val(a, ln)})")
            elif kw in ("rename", "move"):
                m = re.fullmatch(rf'{kw} (\w+) where (\w+) is ("[^"]*"|\w+) to ("[^"]*"|\w+)', s)
                if not m:
                    ex = "new_name" if kw == "rename" else '"work"'
                    raise CompileError(ln, f"не зрозумів {kw}",
                                       f"форма: {kw} files where name is name to {ex}")
                t, w, a, b = m.groups()
                field = "name" if kw == "rename" else "folder"
                fields([w, field], table(t, ln), ln)
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
    code = [f'@app.command("{name}", {args!r}, "{role}")',
            f"async def cmd_{name}(ctx{''.join(', ' + a for a in args)}):"]
    code += ["    " + c for c in out]
    return "\n".join(code), []


def compile_card(n, table, body, stores, cmd_args):
    """Картка таблиці: заголовок, поля, кнопки. Повертає (опис, помилки)."""
    if table not in stores:
        return None, [CompileError(n, f"немає таблиці «{table}»", suggest(table, stores)).text]
    allowed = stores[table]
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
    name, db, stores, roles, cmds, cards = "Bot", ":memory:", {}, {}, [], {}
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
            elif kw == "store":
                m = re.fullmatch(r"store ([a-z_]\w*):", head)
                if not m or not body:
                    raise CompileError(n, "форма: store files: і нижче поля через кому")
                fs = [f.split("=")[0].strip() for _, l in body for f in l.split(",")]
                stores[m.group(1)] = fs
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
                cmds.append((n, m.group(1), (m.group(2) or "").split(), body))
            elif kw == "card":
                m = re.fullmatch(r"card ([a-z_]\w*):", head)
                if not m or not body:
                    raise CompileError(n, "форма: card files: і нижче title, show, button")
                cards[m.group(1)] = (n, body)
            else:
                raise CompileError(n, f"невідоме слово «{kw}»", suggest(kw, TOP))
        except CompileError as e:
            errors.append(e.text)

    code = ["# згенеровано BotFab", f"app = Runtime({name!r}, {db!r})",
            f"app.roles.update({roles!r})"]
    for t, fs in stores.items():
        code.append(f"{t} = app.store({t!r}, {fs!r})")
    # команди й картки компілюємо в кінці, коли вже відомі всі таблиці
    for n, cname, args, body in cmds:
        cmd_code, cmd_errors = compile_command(n, cname, args, body, stores)
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

    if errors:
        return None, errors
    return "\n".join(code) + "\n", []


def check(src):
    return compile_bot(src)
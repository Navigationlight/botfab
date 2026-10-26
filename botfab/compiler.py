import re

from .common import ROLES, CompileError, suggest

TOP = ["bot", "database", "store", "roles", "command"]
STATEMENTS = ["only", "find", "show", "add", "reply", "delete", "rename", "move", "undo", "redo"]


def split_blocks(src):
    blocks, cur = [], None
    for n, line in enumerate(src.splitlines(), 1):
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line[0] not in " \t":
            cur = (n, line.strip(), [])
            blocks.append(cur)
        elif cur is None:
            raise CompileError(n, "відступ без заголовка зверху")
        else:
            cur[2].append((n, line.strip()))
    return blocks


def compile_command(n, name, args, body, stores):
    role, out, found = "member", [], False  # за замовчуванням закрито для гостей

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
        raise CompileError(n, f"команда «{name}» порожня", "додай хоча б reply \"...\"")

    for ln, s in body:
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
            found = True
        elif kw == "show":
            if s != "show found" or not found:
                raise CompileError(ln, "нема що показувати", "спочатку зроби find, потім: show found")
            out.append("await ctx.show(found)")
        elif kw == "add":
            m = re.fullmatch(r"add (\w+) with (.+)", s)
            if not m:
                raise CompileError(ln, "не зрозумів add", 'форма: add files with name = name, tag = "x"')
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
                raise CompileError(ln, "не зрозумів delete", "форма: delete files where name is name")
            t, w, a = m.groups()
            fields([w], table(t, ln), ln)
            out.append(f"await {t}.delete(ctx, {w!r}, {val(a, ln)})")
        elif kw in ("rename", "move"):
            m = re.fullmatch(rf'{kw} (\w+) where (\w+) is ("[^"]*"|\w+) to ("[^"]*"|\w+)', s)
            if not m:
                raise CompileError(ln, f"не зрозумів {kw}",
                                   f"форма: {kw} files where name is name to " + ('new_name' if kw == 'rename' else '"work"'))
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

    code = [f'@app.command("{name}", {args!r}, "{role}")',
            f"async def cmd_{name}(ctx{''.join(', ' + a for a in args)}):"]
    code += ["    " + c for c in out]
    return "\n".join(code)


def compile_bot(src):
    name, db, stores, roles, cmds = "Bot", ":memory:", {}, {}, []
    for n, head, body in split_blocks(src):
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
                raise CompileError(n, f"база «{m.group(1)}» ще не підтримується", "зараз є sqlite")
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
                    raise CompileError(ln, "форма: owner 12345", "ролі: owner, member")
                roles[int(m.group(2))] = m.group(1)
        elif kw == "command":
            m = re.fullmatch(r'command "([a-z_]\w*)"(?: with ([a-z_ ]+))?:', head)
            if not m:
                raise CompileError(n, 'форма: command "find" with text:')
            cmds.append((n, m.group(1), (m.group(2) or "").split(), body))
        else:
            raise CompileError(n, f"невідоме слово «{kw}»", suggest(kw, TOP))

    code = ["# згенеровано BotFab", f"app = Runtime({name!r}, {db!r})", f"app.roles.update({roles!r})"]
    for t, fs in stores.items():
        code.append(f"{t} = app.store({t!r}, {fs!r})")
    for n, cname, args, body in cmds:
        code.append("")
        code.append(compile_command(n, cname, args, body, stores))
    return "\n".join(code) + "\n"


def check(src):
    try:
        return compile_bot(src), []
    except CompileError as e:
        return None, [str(e)]
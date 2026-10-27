import asyncio
import sqlite3

from botfab.compiler import check
from botfab.runtime import Runtime


def build(src):
    py, errors = check(src)
    assert errors == [], errors
    ns = {"Runtime": Runtime}
    exec(py, ns)
    return ns["app"]


def say(app, text, uid=2):
    app._hits.clear()  # у тестах ліміт запитів не заважає
    return [m.text for m in asyncio.run(app.handle(uid, text))]


UNIQUE = (
    'store files:\n    name unique, tag\n\nroles:\n    owner 2\n\n'
    'command "add" with name tag:\n    add files with name = name, tag = tag\n    reply "ok"\n\n'
    'command "remove" with name:\n    delete files where name is name\n    reply "removed"\n\n'
    'command "rename" with old new:\n    rename files where name is old to new\n    reply "renamed"\n\n'
    'command "undo":\n    undo\n\n'
    'command "all":\n    find files where name contains ""\n    show found\n'
)


def test_migration_adds_columns_and_keeps_data(tmp_path):
    db = str(tmp_path / "old.db")
    con = sqlite3.connect(db)  # стара база: таблиця без полів tag і folder
    con.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, name TEXT, _hidden INTEGER DEFAULT 0)")
    con.execute("INSERT INTO files (name) VALUES ('old.pdf')")
    con.commit()
    con.close()
    src = (f'database sqlite "{db}"\nstore files:\n    name, tag, folder\n\nroles:\n    owner 2\n\n'
           'command "add" with name tag:\n    add files with name = name, tag = tag, folder = "inbox"\n    reply "ok"\n\n'
           'command "all":\n    find files where name contains ""\n    show found\n')
    app = build(src)
    assert say(app, "/add new.pdf work") == ["ok"]
    out = say(app, "/all")[0]
    assert "old.pdf" in out and "new.pdf" in out


def test_unique_blocks_duplicates_and_shows_id():
    app = build(UNIQUE)
    assert say(app, "/add a.pdf x") == ["ok"]
    assert "уже є" in say(app, "/add a.pdf y")[0]
    assert say(app, "/all")[0].startswith("• #1 a.pdf")


def test_unique_rename_conflict_and_undo_conflict_are_friendly():
    app = build(UNIQUE)
    say(app, "/add a.pdf x")
    say(app, "/add b.pdf x")
    assert "унікальним" in say(app, "/rename a.pdf b.pdf")[0]       # перейменування в наявне ім'я
    say(app, "/remove a.pdf")
    say(app, "/add a.pdf z")                                         # те саме ім'я вже інший запис
    assert "унікальним" in say(app, "/undo")[0]                      # undo не може повернути старий
    assert "Щось пішло не так" not in say(app, "/all")[0]


def test_delete_by_id_removes_only_one():
    src = ('store tasks:\n    title\n\nroles:\n    owner 2\n\n'
           'command "add" with title:\n    add tasks with title = title\n    reply "ok"\n\n'
           'command "done" with id:\n    delete tasks where id is id\n    reply "done"\n\n'
           'command "all":\n    find tasks where title contains ""\n    show found\n')
    app = build(src)
    say(app, "/add same")
    say(app, "/add same")
    assert say(app, "/done 1") == ["done"]
    out = say(app, "/all")[0]
    assert "#2 same" in out and "#1" not in out
    assert say(app, "/done 99") == ["Нічого не знайдено."]


def test_sql_reserved_words_as_names_work():
    src = ('store group:\n    order, select\n\nroles:\n    owner 2\n\n'
           'command "add" with order select:\n    add group with order = order, select = select\n    reply "ok"\n\n'
           'command "all":\n    find group where order contains ""\n    show found\n')
    app = build(src)
    assert say(app, "/add 1 two") == ["ok"]
    assert "#1 1 | two" in say(app, "/all")[0]


def test_bad_field_names_and_properties():
    _, e = check("store t:\n    Name\n")
    assert "некоректна назва" in e[0]
    _, e = check("store t:\n    id\n")
    assert "зарезервоване" in e[0]
    _, e = check("store t:\n    name uniqe\n")
    assert "unique" in e[0]
    _, e = check('store t:\n    name"; DROP TABLE t; --\n')
    assert e and "некоректна назва" in e[0]


def test_removing_unique_drops_the_index(tmp_path):
    db = str(tmp_path / "u.db")
    head = f'database sqlite "{db}"\n'
    body = ('\nroles:\n    owner 2\n\ncommand "add" with name:\n    add files with name = name\n    reply "ok"\n')
    app1 = build(head + "store files:\n    name unique\n" + body)
    say(app1, "/add a")
    app1.conn.close()
    app2 = build(head + "store files:\n    name\n" + body)  # unique прибрали
    say(app2, "/add a")
    n = app2.conn.execute("SELECT COUNT(*) FROM files WHERE name='a'").fetchone()[0]
    assert n == 2
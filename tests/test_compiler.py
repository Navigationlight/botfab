from botfab.compiler import check


def test_typo_gives_hint():
    src = 'store files:\n    name\n\ncommand "f" with t:\n    fnd files where name contains t\n'
    _, errors = check(src)
    assert "find" in errors[0]


def test_good_code_compiles():
    src = open("examples/archive.bfab", encoding="utf-8").read()
    py, errors = check(src)
    assert errors == []
    assert "app.command" in py
def test_all_errors_at_once():
    src = (
        'store files:\n    name\n\n'
        'command "f" with t:\n'
        '    fnd files where name contains t\n'
        '    reply "Привіт {x}"\n'
    )
    _, errors = check(src)
    assert len(errors) == 2
def test_card_button_unknown_command():
    src = (
        'store files:\n    name\n\n'
        'command "remove" with name:\n    delete files where name is name\n\n'
        'card files:\n    button "x" runs remov\n'
    )
    _, errors = check(src)
    assert "remove" in errors[0]
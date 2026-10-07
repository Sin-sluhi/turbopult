# -*- coding: utf-8 -*-
"""Сборка index.html из частей.

_head.html  — стили и разметка
_js.html    — логика интерфейса
_skin.js    — переключатель обликов (встаёт вместо старого переключателя темы)
_live.js    — связь с бэкендом (встаёт перед блоком запуска)
"""
import pathlib
import sys

web = pathlib.Path(sys.argv[1])
js = (web / "_js.html").read_text(encoding="utf-8")

skin = (web / "_skin.js").read_text(encoding="utf-8")
start = '$("#themeBtn").onclick'
end = 'if(saved) document.documentElement.setAttribute("data-theme", saved);'
if start in js:
    i, j = js.index(start), js.index(end) + len(end)
    js = js[:i] + skin.strip() + js[j:]

live = (web / "_live.js").read_text(encoding="utf-8")
marker = "/* ================= старт ================= */"
if marker not in js:
    raise SystemExit("не найден блок запуска — некуда вставлять живой режим")
js = js.replace(marker, live.strip() + "\n\n" + marker)

head = (web / "_head.html").read_text(encoding="utf-8")
out = head.rstrip() + "\n\n" + js.lstrip()
(web / "index.html").write_text(out, encoding="utf-8")
print("собрано, строк:", len(out.splitlines()))

# Демо для GitHub Pages: тот же интерфейс, но с полноценной обёрткой
# страницы. Без <!doctype> браузер уходит в quirks-режим (см. main.py).
# Бэкенда там нет, поэтому страница сама остаётся в демо-режиме.
docs = web.parent / "docs"
docs.mkdir(exist_ok=True)
page = (
    '<!doctype html>\n<html lang="ru">\n<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '</head>\n<body>\n' + out + '\n</body>\n</html>\n'
)
(docs / "index.html").write_text(page, encoding="utf-8")
(docs / ".nojekyll").write_text("", encoding="utf-8")
print("демо для GitHub Pages: docs/index.html")

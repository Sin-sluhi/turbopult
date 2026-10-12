"""Перевод замечаний checkxml.gge.ru на язык сметчика.

Правило «только голая правда»: объяснения и шаги берутся лишь из базы
знаний ИИ-помощника (gge_kb.json, собирается build_gge_kb.py из
C:\\TelegramBot\\AI_KB): рецепты «Ошибка_NN», таблица проверок КПСР,
русские описания полей из XSD-схем ГГЭ. Если для ошибки ничего нет —
честно говорим, что её ещё не разбирали, и путь в меню не выдумываем.
"""
import difflib
import json
import pathlib
import re

_KB_PATH = pathlib.Path(__file__).with_name("gge_kb.json")
_kb = None


def kb() -> dict:
    global _kb
    if _kb is None:
        try:
            _kb = json.loads(_KB_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _kb = {"recipes": [], "kpsr": [], "groups": [], "xsd": {}}
    return _kb


def _norm(s: str) -> str:
    s = str(s or "").lower().replace("ё", "е")
    s = re.sub(r"\d{4}\.\d\d\.\d\d|\d+", "#", s)
    return re.sub(r"[^\w#]+", " ", s).strip()


# ---------------------------------------------------------------- поля XSD
_GENERIC = {"Code", "Name", "Num", "Type", "Value", "Target", "Date", "Reason", "Total", "Price",
            "Unit", "Quantity", "Note", "Final", "Values", "Link", "ID", "Main"}


def field_doc(name: str, parent: str = "") -> str:
    docs = kb()["xsd"].get(name) or []
    if not docs:
        return ""
    if parent:
        # в схемах родитель бывает элементом (Item) или типом (TItem)
        for d in docs:
            segs = d["path"].split("/")
            if len(segs) > 1 and segs[-2] in (parent, "T" + parent, parent + "Type"):
                return d["doc"]
    # общие имена (Code, Name, Num…) без контекста не угадываем
    if name in _GENERIC and parent:
        return ""
    return docs[0]["doc"]


def field_label(name: str, parent: str = "") -> str:
    """Короткое человеческое имя поля: первое предложение описания из XSD."""
    doc = field_doc(name, parent)
    if not doc:
        return ""
    doc = re.split(r"(?<=[^.]{12})(?:[.;:]\s|\s\()", doc)[0]
    doc = re.split(r"\s+в соответствии с|\s+в локальном сметном расч", doc)[0]
    return doc.strip().strip('"«»').rstrip(".,")


def _name(el: str, parent: str = "") -> str:
    lab = field_label(el, parent)
    return f"«{lab}» ({el})" if lab else f"«{el}»"


# ---------------------------------------------------------------- рецепты
_IMPORT = re.compile(r"Импорт из|Параметры импорта|когда читает файл|импортир", re.I)


def _recipe_for(parent: str, child: str):
    best, score = None, 0
    for r in kb()["recipes"]:
        if r["num"] < 50 and r["num"] != 13:
            continue
        for p, c in r["pairs"]:
            s = 0
            if child and c == child and p == parent:
                s = 3
            elif child and c == child and child not in _GENERIC and (not p or not parent):
                s = 2
            elif child and c == child and child not in _GENERIC:
                s = 1.5
            elif not child and p == parent:
                s = 1
            if s > score:
                best, score = r, s
    return best if score >= 1.5 else None


_ACTION = re.compile(r"задаётся|задать|задайте|заполн|укажите|указать|проверьте|откройте|нажмите|выберите|"
                     r"примените|пересчитай|включите|снимите|прогоните|\(F\d+\)|Ctrl\+|«[^»]+»\s*>", re.I)
_SCHEMA = re.compile(r"по схеме|В схеме|схема требует|Точные тексты", re.I)


def _steps_from(r) -> list:
    """Из рецепта — только действия в смете: шаги про импорт и пересказ
    схемы пропускаем, оставляем шаги с «нажмите / заполните / F2 / Ctrl+K…»."""
    steps = [s for s in r["steps"] if not _IMPORT.search(s)]
    act = [s for s in steps if _ACTION.search(s) and not _SCHEMA.search(s.split(".")[0])]
    out = act[-2:] or [s for s in steps if not _SCHEMA.search(s)][-1:] or steps[-1:]
    return out + [s for s in r["short"] if s not in out][:2]


# ---------------------------------------------------------------- XSD
_RX = [
    ("enum", re.compile(r"значение '(?P<val>.*?)' не является фасетно-допустимым по отношению к перечислению '\[(?P<allowed>.*?)\]'")),
    ("badval", re.compile(r"значение '(?P<val>.*?)' элемента '(?P<el>[^']+)' недействительно")),
    ("type", re.compile(r"'(?P<val>.*?)' не является допустимым значением для '(?P<type>[^']+)'")),
    ("missing", re.compile(r"[Нн]едействительный контент был обнаружен, начиная с элемента '(?P<at>[^']+)'\. Ожидается один из '\{(?P<exp>[^}]*)\}'")),
    ("incomplete", re.compile(r"содержимое элемента '(?P<el>[^']+)' не является полным\. Ожидается один из '\{(?P<exp>[^}]*)\}'")),
    ("extra", re.compile(r"недопустимый контент, начиная с элемента '(?P<at>[^']+)'\. На этом этапе дочерний элемент не ожидается")),
    ("noschema", re.compile(r"Отсутствует соответствующая XSD-схема")),
]
_TYPES = {"integer": "целое число", "decimal": "число", "date": "дата", "string": "текст", "boolean": "да/нет"}


def parse_xsd(text: str) -> dict:
    m = re.search(r"\((\d+),\s*(\d+)\)\s*$", text)
    out = {"line": int(m.group(1)) if m else 0, "col": int(m.group(2)) if m else 0}
    for kind, rx in _RX:
        mm = rx.search(text)
        if mm:
            out["kind"] = kind
            out.update({k: v for k, v in mm.groupdict().items() if v is not None})
            return out
    out["kind"] = "other"
    return out


def explain_xsd(p: dict, ctx: dict) -> dict:
    """p — разбор сообщения (с объединённой парой enum+badval), ctx — где в файле."""
    kind = p["kind"]
    parent = ctx.get("parent", "")
    where = ctx.get("where", "")
    el = p.get("el") or ctx.get("element", "")
    res = {"rule": "xsd-" + kind}

    if kind == "noschema":
        r = _recipe_by_num(13)
        res.update(title="Сайт не узнал формат файла",
                   plain="Для выбранного типа сметы у сайта нет подходящей XSD-схемы. Обычно это значит, что "
                         "выбран не тот тип документа или файл выгружен в старой версии схемы.",
                   fix=(["Проверьте тип сметы вверху (или оставьте «Определить сам») и загрузите файл ещё раз."]
                        + (_steps_from(r) if r else [])))
        return res

    if kind in ("enum", "badval", "type"):
        val = p.get("val", "")
        allowed = [a.strip() for a in p.get("allowed", "").split(",") if a.strip()]
        if val == "":
            res["title"] = f"Не заполнено поле {_name(el, parent)}"
            res["plain"] = (f"Поле пустое{', ' + where if where else ''}, а схема требует значение"
                            + (f" — {_TYPES.get(p.get('type', ''), p.get('type', ''))}." if p.get("type") else "."))
        else:
            res["title"] = f"Недопустимое значение «{val}» в поле {_name(el, parent)}"
            res["plain"] = (f"В файле{(' ' + where) if where else ''} указано «{val}»"
                            + (f", а схема принимает только: {', '.join(allowed)}." if allowed else
                               f", а нужно {_TYPES.get(p.get('type', ''), 'другое значение')}."))
        doc = field_doc(el, parent)
        if doc:
            res["plain"] += f" По схеме ГГЭ это поле — {doc[:300].rstrip('.')}."

    elif kind in ("missing", "incomplete"):
        exp = [e.strip() for e in p.get("exp", "").split(",") if e.strip()]
        el = exp[0] if len(exp) == 1 else (exp[0] if exp else "")
        names = ", ".join(_name(e, parent) for e in exp[:4])
        if kind == "missing":
            res["title"] = f"Не хватает обязательного поля {names}" if len(exp) == 1 else f"Не хватает одного из полей: {names}"
            res["plain"] = (f"В файле{(' ' + where) if where else ''} перед «{p.get('at')}» должно стоять "
                            f"{'поле ' + names if len(exp) == 1 else 'одно из полей ' + names}, а его нет. "
                            "Обычно это значит, что в смете эти данные не заполнены и плагин их не выгрузил.")
        else:
            chain = ctx.get("chain") or []
            el_parent = chain[chain.index(p.get("el")) - 1] if p.get("el") in chain[1:] else ""
            res["title"] = f"Не заполнено до конца: в {_name(p.get('el', ''), el_parent)} нет {names}"
            res["plain"] = (f"Блок «{p.get('el')}»{(' ' + where) if where else ''} выгружен не полностью — "
                            f"в нём не хватает {names}.")
            parent = p.get("el", parent)
        doc = field_doc(el, parent) if el else ""
        if doc:
            res["plain"] += f" По схеме ГГЭ это — {doc[:300].rstrip('.')}."

    elif kind == "extra":
        el = p.get("at", "")
        res["title"] = f"Лишнее поле «{el}» — схема его здесь не ждёт"
        res["plain"] = (f"В файле{(' ' + where) if where else ''} стоит «{el}», которого нет в этом месте схемы. "
                        "Чаще всего так бывает, когда файл выгружен в другой версии схемы, чем принимает сайт.")
        r = _recipe_by_num(13)
        if r:
            res["fix"] = _steps_from(r)
            res["ref"] = r["file"]
        return res
    else:
        res["title"] = ""
        res["plain"] = ""

    r = _recipe_for(parent, el) if el else None
    if r:
        res["fix"] = _steps_from(r)
        res["ref"] = r["file"]
        if r.get("important"):
            res["important"] = r["important"]
    else:
        c = _kpsr_for_field(el, parent, presence=kind in ("missing", "incomplete") or not p.get("val"))
        if c:
            res["fix"] = _split_solution(c["solution"])
            res["ref"] = "Ошибки_КПСР_и_решения.xlsx · " + c["name"]
    return res


def _stems(s: str) -> set:
    return {w[:5] for w in _norm(s).split() if len(w) > 3 and w not in ("позиции", "локального", "сметного", "расчета")}


def _kpsr_for_field(el: str, parent: str, presence: bool):
    """Нет рецепта под поле — ищем проверку КПСР с тем же названием поля
    («Проверка наличия накладных расходов» для Overheads) и берём её решение."""
    label = re.sub(r"\s*\(.*$", "", field_label(el, parent))   # пояснение в скобках не сравниваем
    want = _stems(label)
    if not want:
        return None
    best, score = None, 0.0
    for c in kb()["kpsr"]:
        name = c["name"]
        if presence and not re.match(r"Проверка наличия", name):
            continue
        got = _stems(re.sub(r"^Проверка (наличия|значения)\s+", "", name))
        if not got:
            continue
        sc = len(want & got) / min(len(want), len(got))
        if sc > score:
            best, score = c, sc
    return best if score >= 0.99 else None


def _recipe_by_num(n):
    return next((r for r in kb()["recipes"] if r["num"] == n), None)


# ---------------------------------------------------------------- сверка с СНБ
def _split_solution(s: str) -> list:
    parts = [x.strip() for x in re.split(r"(?<=[.!])\s+(?=[А-ЯЁ«])", str(s or "")) if x.strip()]
    return parts[:5]


def explain_snb(item: dict) -> dict:
    text = _norm(item.get("text"))
    best, score = None, 0.0
    for c in kb()["kpsr"]:
        for cand in (c["verbatim"], c["name"]):
            if not cand:
                continue
            s = difflib.SequenceMatcher(None, text, _norm(cand)).ratio()
            if s > score:
                best, score = c, s
    if best and score >= 0.62:
        return {"rule": "snb-" + best["key"], "title": best["name"], "plain": best["meaning"],
                "fix": _split_solution(best["solution"]), "ref": "Ошибки_КПСР_и_решения.xlsx"}
    return {"rule": "snb", "title": "", "plain": "", "fix": []}


def explain_text(item: dict) -> dict:
    """Человеческие фразы сайта (например, «Не указан номер…») — ищем рецепт с таким текстом."""
    text = _norm(item.get("text"))
    best, score = None, 0.0
    for r in kb()["recipes"]:
        for t in r["texts"] + [r["title"]]:
            sc = difflib.SequenceMatcher(None, text, _norm(t)).ratio()
            if sc > score:
                best, score = r, sc
    if best and score >= 0.6:
        return {"rule": "rec-" + str(best["num"]), "title": item.get("text"), "plain": "",
                "fix": _steps_from(best), "ref": best["file"]}
    # «Не указан номер локального сметного расчета» — ищем поле с таким описанием в XSD
    m = re.match(r"(?:Не указан[аоы]?|Отсутству\w+|Не заполнен[аоы]?)\s+(.+)", str(item.get("text") or ""), re.I)
    if m:
        want = _norm(m.group(1))
        hit, hs = None, 0.0
        for name, docs in kb()["xsd"].items():
            for d in docs:
                lab = re.split(r"(?:[.;:]\s|\s\()", d["doc"])[0]
                sc = difflib.SequenceMatcher(None, want, _norm(lab)).ratio()
                if sc > hs:
                    hit, hs = (name, d["path"].split("/")[-2] if "/" in d["path"] else ""), sc
        if hit and hs >= 0.75:
            name, parent = hit
            r = _recipe_for(parent.lstrip("T"), name)
            return {"rule": "html-" + name, "title": item.get("text"),
                    "plain": f"В смете не заполнено поле {_name(name, parent)}, поэтому сайт не может показать документ целиком.",
                    "fix": _steps_from(r) if r else [], "ref": r["file"] if r else ""}
    return {"rule": "", "title": "", "plain": "", "fix": []}


def explain(item: dict) -> dict:
    if item.get("source") == "snb":
        return explain_snb(item)
    if item.get("source") in ("html", "xslt"):
        return explain_text(item)
    return {"rule": "", "title": "", "plain": "", "fix": []}

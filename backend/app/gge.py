"""Проверка сметы на checkxml.gge.ru и объяснение ошибок простым языком.

Сайт Главгосэкспертизы принимает XML одним запросом POST /check
(multipart: file, bypass, slot) и возвращает JSON со списками ошибок:
messagesXsd — схема XSD, messagesXsdUser — логика XSD, snb.description —
сверка со сметно-нормативной базой (позиция, текст, полученное и
ожидаемое значение), messagesXslt/messagesHtml — преобразование в HTML.
Пульт отправляет файл, разбирает ответ и к каждой ошибке подбирает
объяснение из gge_rules.py: что это значит и что нажать в Турбосметчике.
"""
import logging
import re
import xml.parsers.expat

import httpx

from . import gge_rules

log = logging.getLogger("uvicorn.error")

CHECK_URL = "https://checkxml.gge.ru/check"
MAX_BYTES = 60 * 1024 * 1024

# Тип документа на сайте («слот»). Для смет — пять вариантов.
SLOTS = {
    "ObjectAndLocalEstimates": "Локальные и объектные сметы",
    "IndividualEstimates": "Индивидуальные сметные нормативы",
    "DesignAndSurveyEstimates": "Сметы на проектные и изыскательские работы",
    "AllEstimatesCapitalConstruction": "Сводный сметный расчёт",
    "DismantlingEstimates": "Сметы на демонтаж (снос)",
}


def guess_slot(data: bytes) -> str:
    """Тип сметы по корню XML — чтобы пользователю не выбирать вручную."""
    head = data[:4000].decode("utf-8", "ignore")
    if re.search(r"<\s*(SummaryEstimate|AllEstimates|CapitalConstruction)", head, re.I):
        return "AllEstimatesCapitalConstruction"
    if re.search(r"<\s*DesignAndSurvey|ПИР|Изыскани", head, re.I):
        return "DesignAndSurveyEstimates"
    if re.search(r"Dismantl", head, re.I):
        return "DismantlingEstimates"
    if re.search(r"<\s*Individual", head, re.I):
        return "IndividualEstimates"
    return "ObjectAndLocalEstimates"


async def _post(data: bytes, filename: str, slot: str) -> dict:
    files = {"file": (filename or "smeta.xml", data, "text/xml")}
    form = {"bypass": "false", "slot": slot}
    last = None
    # Сайт российский — идём напрямую, без VPN; не вышло — через прокси окружения
    for trust_env in (False, True):
        try:
            async with httpx.AsyncClient(timeout=120, trust_env=trust_env) as c:
                r = await c.post(CHECK_URL, data=form, files=files)
            if r.status_code >= 400:
                raise RuntimeError(f"Сайт проверки ответил {r.status_code}")
            return r.json()
        except httpx.TransportError as e:
            last = e
        except ValueError:
            raise RuntimeError("Сайт проверки вернул непонятный ответ — попробуйте ещё раз")
    raise RuntimeError(f"Сайт checkxml.gge.ru недоступен ({last.__class__.__name__})")


def _clean(msg: str) -> str:
    msg = re.sub(r"</?pre>", "", str(msg or ""))
    msg = re.sub(r"<[^>]+>", "", msg)
    return re.sub(r"\s+", " ", msg).strip()


def _items(res: dict) -> list[dict]:
    """Все замечания сайта в одном списке: откуда, тип, текст, детали."""
    out = []
    for m in res.get("messagesXsd") or []:
        out.append({"source": "xsd", "kind": "error", "text": _clean(m)})
    for m in res.get("messagesXsdUser") or []:
        out.append({"source": "logic", "kind": "error", "text": _clean(m)})
    snb = res.get("snb") or {}
    for d in snb.get("description") or []:
        out.append({
            "source": "snb",
            "kind": "warning" if d.get("typ") == "warning" else "error",
            "text": _clean(d.get("text")),
            "position": _clean(d.get("position")),
            "got": _clean(d.get("from")),
            "expected": _clean(d.get("to")),
        })
    for m in res.get("messagesXslt") or []:
        if isinstance(m, dict):
            out.append({"source": "xslt", "kind": "error", "text": _clean(m.get("message")),
                        "position": f"строка {m.get('line')}"})
    for m in res.get("messagesHtml") or []:
        t = _clean(m)
        # кроме фраз, сайт присылает сюда куски скрипта — их не показываем
        if t and not re.search(r"[=;{}]|\bblank\b", t):
            out.append({"source": "html", "kind": "error", "text": t})
    return [o for o in out if o["text"]]


def _verdict(res: dict, items: list[dict]) -> dict:
    snb = res.get("snb") or {}
    errors = sum(1 for i in items if i["kind"] == "error")
    warnings = sum(1 for i in items if i["kind"] == "warning")
    ok = bool(res.get("isValidXsd")) and errors == 0
    return {
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "xsd_ok": bool(res.get("isValidXsd")),
        "snb_ok": snb.get("success") == "true",
        "snb_checked": snb.get("checked_snb"),
        "schema": res.get("schemaXsd") or "",
        # файл в старой схеме (1.xx) — сайт сравнивает со старым XSD, отсюда лавина ошибок
        "old_schema": bool(re.search(r"-1_\d\d", res.get("schemaXsd") or "")),
        "doc_type": res.get("docType") or "",
    }


# ---------------------------------------------------------------- где в файле
_HOLDERS = {"Item": "позиции", "Section": "разделе", "Object": "объекте", "Estimate": "смете",
            "Resource": "ресурсе", "Work": "строке ВОР", "LocalEstimate": "строке объектного расчёта"}


def _map_lines(data: bytes):
    """Разбирает XML и запоминает, какой элемент начинается на какой строке,
    а у позиций и разделов — номер, шифр и наименование."""
    starts, stack, holders = [], [], {}
    text = {"buf": ""}
    p = xml.parsers.expat.ParserCreate()

    def start(name, attrs):
        node = {"name": name, "line": p.CurrentLineNumber, "id": len(starts),
                "parent": stack[-1] if stack else None}
        starts.append(node)
        stack.append(node)
        text["buf"] = ""
        if name in _HOLDERS:
            holders[node["id"]] = {}

    def end(name):
        node = stack.pop()
        val = text["buf"].strip()
        # номер, шифр и наименование лежат прямо в позиции или на уровень
        # глубже (Item > Cost > Code) — поднимаемся не выше двух уровней
        h = node["parent"]
        # у позиции шифр и наименование лежат в Cost — разрешаем один шаг вверх
        if h is not None and h["id"] not in holders and h["parent"] is not None                 and h["parent"]["name"] == "Item" and h["parent"]["id"] in holders:
            h = h["parent"]
        if h is not None and h["id"] in holders and name in ("Num", "Code", "Name") and val:
            holders[h["id"]].setdefault(name, val[:120])
        text["buf"] = ""

    def chars(d):
        text["buf"] += d

    p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, chars
    try:
        p.Parse(data, True)
    except xml.parsers.expat.ExpatError:
        pass
    return starts, holders


def _where(starts, holders, line: int) -> dict:
    node = None
    for n in starts:
        if n["line"] <= line:
            node = n
        else:
            break
    if not node:
        return {}
    parent = node["parent"]["name"] if node["parent"] else ""
    # ближайший «держатель» вверх по дереву: позиция, раздел, объект…
    h = node
    while h and h["id"] not in holders:
        h = h["parent"]
    where = ""
    if h:
        info = holders[h["id"]]
        label = _HOLDERS[h["name"]]
        num = info.get("Num") or (info.get("Code") if h["name"] == "Section" else "")
        desc = " ".join(x for x in (info.get("Code") if h["name"] != "Section" else "", info.get("Name")) if x)
        where = f"в {label}" + (f" {num}" if num else "") + (f" «{desc[:90]}»" if desc else "")
    chain, n = [], node
    while n:
        chain.insert(0, n["name"])
        n = n["parent"]
    return {"element": node["name"], "parent": parent, "where": where, "chain": chain}


def _merge_xsd(items: list[dict]) -> list[dict]:
    """Пара «значение не из перечисления» + «значение элемента недействительно»
    на одной строке — это одна ошибка: склеиваем."""
    out = {}
    for it in items:
        p = gge_rules.parse_xsd(it["text"])
        key = (p["line"], p["col"]) if p["line"] else ("t", it["text"])
        if key in out:
            prev = out[key]
            for k, v in p.items():
                prev["parsed"].setdefault(k, v)
            if p["kind"] == "enum":
                prev["parsed"]["kind"] = "enum"
                prev["parsed"]["allowed"] = p.get("allowed", "")
            prev["text"] += "\n" + it["text"]
        else:
            it = dict(it, parsed=p)
            out[key] = it
    return list(out.values())


async def check(data: bytes, filename: str, slot: str = "") -> dict:
    if not data:
        raise ValueError("Пустой файл")
    if len(data) > MAX_BYTES:
        raise ValueError("Файл больше 60 МБ")
    if not filename.lower().endswith((".xml", ".gge", ".sig", ".zip")) and b"<?xml" not in data[:200]:
        raise ValueError("Нужен XML-файл сметы — тот, что выгружен из Турбосметчика для экспертизы")
    slot = slot if slot in SLOTS else guess_slot(data)
    res = await _post(data, filename, slot)
    items = _items(res)
    starts, holders = _map_lines(data)
    xsd = _merge_xsd([i for i in items if i["source"] in ("xsd", "logic")])
    for it in xsd:
        p = it.pop("parsed")
        ctx = _where(starts, holders, p["line"]) if p.get("line") else {}
        if p.get("line"):
            it["position"] = f"строка {p['line']}" + (", " + ctx["where"] if ctx.get("where") else "")
        it.update(gge_rules.explain_xsd(p, ctx))
    rest = [i for i in items if i["source"] not in ("xsd", "logic")]
    for it in rest:
        it.update(gge_rules.explain(it))
    items = xsd + rest
    return {"slot": slot, "slot_name": SLOTS[slot], "summary": _verdict(res, items),
            "items": items, "file": filename}

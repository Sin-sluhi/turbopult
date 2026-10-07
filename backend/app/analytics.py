"""Вопросы к базе обращений обычным языком.

«Сколько пользователей написало про драйвер в сентябре?»,
«сколько разговоров закончилось на пятой минуте в августе?»,
«обращения по темам за месяц» — строка в архиве отвечает цифрой,
таблицей или графиком.

Два движка:

  * встроенный разбор — работает всегда, без интернета. Понимает
    что считать (пользователей, обращения, сообщения, среднюю длительность),
    за какой период, по какой теме, с какой длительностью разговора
    и как разбить (по месяцам, дням, темам, каналам, специалистам);
  * Claude — если в .env есть ANTHROPIC_API_KEY. Тогда вопрос любой
    формы превращается в SQL-запрос. Встроенный разбор остаётся запасным.

Запросы выполняются на отдельном соединении только для чтения, с белым
списком операций: ни встроенный разбор, ни модель не могут ничего
изменить в базе.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sqlite3

from . import config, folders, moods

log = logging.getLogger("analytics")

# ---------------------------------------------------------------- данные
# Одна плоская «витрина» — и для встроенного разбора, и для модели.
# Длительность разговора — от первого сообщения до последнего реплики
# клиента или специалиста (служебные строки не в счёт).
BASE = """
WITH conv AS (
  SELECT c.id, c.contact_id, c.provider, c.status, c.folder, c.folders, c.assignee,
         c.topic, c.archived_at, k.display_name, k.org, k.license, k.mood,
         t.first_ts, t.last_ts, t.n_in, t.n_out,
         (t.last_ts - t.first_ts) / 60000.0 AS dur_min
  FROM conversations c
  JOIN contacts k ON k.id = c.contact_id
  JOIN (SELECT conversation_id,
               MIN(ts) AS first_ts, MAX(ts) AS last_ts,
               SUM(direction = 'in') AS n_in, SUM(direction = 'out') AS n_out
        FROM messages WHERE direction != 'sys'
        GROUP BY conversation_id) t ON t.conversation_id = c.id
)
"""

SCHEMA_FOR_MODEL = """
Таблица-витрина conv (одна строка = одно обращение), доступна через CTE,
который будет подставлен перед вашим запросом:
  id           — номер обращения
  contact_id   — клиент (человек); «пользователи/клиенты» = COUNT(DISTINCT contact_id)
  provider     — канал: 'telegram' | 'max'
  status       — 'new' | 'open' | 'waiting' | 'closed' | 'archived'
  folder       — основная тема архива (см. список ниже), '' если не в архиве
  folders      — ВСЕ темы обращения, JSON-список: одно обращение может лежать
                 в нескольких папках. Для фильтра и подсчёта по темам
                 используйте json_each(folders), например
                 EXISTS (SELECT 1 FROM json_each(folders) j WHERE j.value = 'arps')
  assignee     — специалист, который вёл обращение
  topic        — тема, указанная при создании (часто пусто)
  archived_at  — когда убрали в архив, unix-мс, 0 если не убрали
  display_name, org, license — имя, организация, номер ключа клиента
  mood         — пометка о манере общения клиента (ключ, см. ниже)
  first_ts, last_ts — первое и последнее сообщение, unix-мс
  n_in, n_out  — сколько сообщений от клиента и от специалиста
  dur_min      — длительность разговора в минутах (last_ts - first_ts)

Сырые таблицы тоже можно читать: messages(id, conversation_id, direction
'in'|'out'|'sys', text, ts, author), contacts, conversations.
Время — unix-миллисекунды; для дат используйте
datetime(first_ts/1000, 'unixepoch', 'localtime').
"""


def _folder_catalog() -> str:
    lines = []
    for g in folders.GROUPS:
        kids = ", ".join(f"{c['key']}={c['name']}" for c in g["children"])
        lines.append(f"  раздел «{g['name']}»: {kids}")
    lines.append("  undef=Не определённая тема")
    mood_list = ", ".join(f"{m['key']}={m['name']}" for m in moods.MOODS)
    return "Темы (folder):\n" + "\n".join(lines) + f"\nПометки (mood): {mood_list}\n"


# ---------------------------------------------------------------- чтение
ALLOWED_OPS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
               getattr(sqlite3, "SQLITE_RECURSIVE", 33)}


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in ALLOWED_OPS else sqlite3.SQLITE_DENY


def _run(sql: str, args: tuple = ()) -> tuple[list[str], list[list]]:
    """Выполняет запрос только на чтение, с потолком по времени и строкам."""
    path = os.path.abspath(config.DB_PATH)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    try:
        conn.set_authorizer(_authorizer)
        steps = {"n": 0}

        def guard():
            steps["n"] += 1
            return 1 if steps["n"] > 20000 else 0   # ~2 млн инструкций — хватит с запасом
        conn.set_progress_handler(guard, 100)
        cur = conn.execute(sql, args)
        cols = [d[0] for d in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(500)]
        return cols, rows
    finally:
        conn.close()


# ---------------------------------------------------------------- встроенный разбор
MONTHS = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август",
          "сентябр", "октябр", "ноябр", "декабр"]
MONTH_NAMES = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
               "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTH_RE = re.compile(r"\b(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяе]|июн\w*|июл\w*|"
                      r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)(?:\s+(20\d\d))?")

# Темы: (регулярка, ключ). Порядок важен — более узкое раньше общего.
TOPIC_RULES = [
    (r"поиск\w* ошибк\w* в смет", "finderr"), (r"ошибк\w* в смет", "finderr"),
    (r"ошибк\w* в сборк|сборк", "err_build"),
    (r"ошибк\w* в турбо|турбосметчик\w* (?:ошиб|пада|вылет)", "err_turbo"),
    (r"ошибк\w* и сбо|сбо[ийе]\w*|ошибок|ошибк", "g_errors"),
    (r"установк\w* и подключ", "g_setup"), (r"работ\w* в программ", "g_work"),
    (r"норматив", "g_norms"), (r"обмен|формат", "g_exchange"),
    (r"методик\w* и консульт", "g_method"),
    (r"эксперт", "gge_exp"), (r"\bgge|\bmge|\bгге|\bмге", "gge"),
    (r"удал[её]н\w*|anydesk|энидеск", "remote"),
    (r"установк|установ", "install"), (r"скача|загруз", "download"),
    (r"драйвер|hasp|ключ\w* защит", "driver"),
    (r"функци", "functions"), (r"свойств", "docprops"),
    (r"формул|итогов\w* начислен|начислен", "formulas"), (r"поправк", "correction"),
    (r"печат", "print"), (r"объектн", "objsmeta"), (r"пояснительн|записк", "explnote"),
    (r"дополнен", "addon"), (r"\bснб", "snb"), (r"индекс", "indexes"),
    (r"тех\s*част|техчаст", "techpart"), (r"\bпир\b", "pir"), (r"\bржд\b", "rzd"),
    (r"вопрос\w* по баз|\bбаз[аеуы]\b|\bбаз\b", "base_q"),
    (r"арпс|\bxml", "arps"), (r"\bвор\b|ведомост\w* объ[её]м", "vor"), (r"прайс", "pricelist"),
    (r"методик", "method"), (r"консультац", "consult"), (r"конъюнктур", "market"),
    (r"нмцк|смет\w* контракт", "nmck"),
    (r"не\s*определ|неопредел|без темы", "undef"),
]

MOOD_RULES = [(r"спокойн", "calm"), (r"разговорчив", "talker"), (r"долг\w* объясн", "slow"),
              (r"спорщ", "arguer"), (r"резк", "touchy"), (r"негатив", "negative")]


def _plural(n, one, few, many):
    n = abs(int(n))
    a, b = n % 100, n % 10
    if 10 < a < 20:
        return many
    if 1 < b < 5:
        return few
    if b == 1:
        return one
    return many


def _ms(d: dt.datetime) -> int:
    return int(d.timestamp() * 1000)


def _period(q: str, now: dt.datetime):
    """Возвращает (с, по, подпись) или None."""
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "сегодня" in q:
        return day0, day0 + dt.timedelta(days=1), "сегодня"
    if "вчера" in q:
        return day0 - dt.timedelta(days=1), day0, "вчера"
    m = re.search(r"за (?:последн\w+ )?(\d+) (дн|недел|месяц)", q)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * (1 if unit == "дн" else 7 if unit == "недел" else 30)
        word = (_plural(n, "день", "дня", "дней") if unit == "дн" else
                _plural(n, "неделю", "недели", "недель") if unit == "недел" else
                _plural(n, "месяц", "месяца", "месяцев"))
        return now - dt.timedelta(days=days), now, f"за {n} {word}"
    if re.search(r"(эт\w+|текущ\w+) недел|за недел", q):
        start = day0 - dt.timedelta(days=day0.weekday())
        return start, now, "на этой неделе"
    if re.search(r"прошл\w+ недел", q):
        start = day0 - dt.timedelta(days=day0.weekday() + 7)
        return start, start + dt.timedelta(days=7), "на прошлой неделе"
    if re.search(r"прошл\w+ месяц", q):
        first = day0.replace(day=1)
        prev = (first - dt.timedelta(days=1)).replace(day=1)
        return prev, first, f"{MONTH_NAMES[prev.month - 1]} {prev.year}"
    if re.search(r"(эт\w+|текущ\w+) месяц|за месяц", q):
        first = day0.replace(day=1)
        return first, now, "в этом месяце"
    if re.search(r"(эт\w+|текущ\w+) год|за год", q):
        return day0.replace(month=1, day=1), now, "в этом году"
    m = MONTH_RE.search(q)
    if m:
        word = m.group(1)
        idx = 4 if re.fullmatch(r"ма[йяе]", word) else next(i for i, s in enumerate(MONTHS) if word.startswith(s))
        year = int(m.group(2)) if m.group(2) else (now.year if idx + 1 <= now.month else now.year - 1)
        start = dt.datetime(year, idx + 1, 1)
        end = dt.datetime(year + (idx == 11), 1 if idx == 11 else idx + 2, 1)
        return start, end, f"{MONTH_NAMES[idx]} {year}"
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b", q)
    if m:
        y = int(m.group(3)) if m.group(3) else now.year
        y = y + 2000 if y < 100 else y
        start = dt.datetime(y, int(m.group(2)), int(m.group(1)))
        return start, start + dt.timedelta(days=1), start.strftime("%d.%m.%Y")
    return None


ORDINALS = [("перв", 1), ("втор", 2), ("трет", 3), ("четверт", 4), ("пятнадцат", 15),
            ("пят", 5), ("шест", 6), ("седьм", 7), ("восьм", 8), ("девят", 9),
            ("десят", 10), ("двадцат", 20), ("тридцат", 30), ("сорок", 40)]


def _duration(q: str):
    """Фильтр длительности разговора: (sql, подпись) или None."""
    # «на пятой минуте» -> «на 5 минуте»
    for stem, n in ORDINALS:
        q = re.sub(r"\b" + stem + r"\w*(?=\s+минут)", str(n), q)
    num = r"(\d+)(?:-?й|-?ой|-?ей)?"
    m = re.search(r"между " + num + r" и " + num + r" минут", q)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return f"dur_min >= {a} AND dur_min < {b}", f"длительностью {a}–{b} мин"
    m = re.search(r"на " + num + r" минут", q)
    if m:
        n = int(m.group(1))
        return f"dur_min > {n - 1} AND dur_min <= {n}", f"завершившихся на {n}-й минуте"
    m = re.search(r"(дольше|больше|более|свыше) " + num + r" минут", q)
    if m:
        n = int(m.group(2))
        return f"dur_min > {n}", f"дольше {n} мин"
    m = re.search(r"(быстрее|меньше|менее|короче|до) " + num + r" минут", q)
    if m:
        n = int(m.group(2))
        return f"dur_min < {n}", f"короче {n} мин"
    if re.search(r"(дольше|больше|более) час", q):
        return "dur_min > 60", "дольше часа"
    return None


def _topics(q: str) -> list[str]:
    found, taken = [], q
    for rx, key in TOPIC_RULES:
        m = re.search(rx, taken)
        if m:
            found.append(key)
            taken = taken[:m.start()] + " " * (m.end() - m.start()) + taken[m.end():]
    return found


def _topic_sql(keys: list[str]) -> tuple[str, str]:
    parts, names = [], []
    for k in keys:
        grp = next((g for g in folders.GROUPS if g["key"] == k), None)
        if grp:
            kids = ",".join(f"'{c['key']}'" for c in grp["children"])
            parts.append(f"EXISTS (SELECT 1 FROM json_each(folders) j WHERE j.value IN ({kids}))")
            names.append(f"раздел «{grp['name']}»")
        else:
            parts.append(f"EXISTS (SELECT 1 FROM json_each(folders) j WHERE j.value = '{k}')")
            names.append(f"«{folders.name_of(k)}»")
    return "(" + " OR ".join(parts) + ")", ", ".join(names)


GROUP_BY = [
    (r"по месяц", "strftime('%Y-%m', first_ts/1000, 'unixepoch', 'localtime')", "Месяц"),
    (r"по недел", "strftime('%Y-нед.%W', first_ts/1000, 'unixepoch', 'localtime')", "Неделя"),
    (r"по дн(ям|ю)(?! недел)|по датам|по числам", "date(first_ts/1000, 'unixepoch', 'localtime')", "День"),
    (r"по дням недел", "CASE strftime('%w', first_ts/1000, 'unixepoch', 'localtime') "
                        "WHEN '1' THEN '1 пн' WHEN '2' THEN '2 вт' WHEN '3' THEN '3 ср' WHEN '4' THEN '4 чт' "
                        "WHEN '5' THEN '5 пт' WHEN '6' THEN '6 сб' ELSE '7 вс' END", "День недели"),
    (r"по час", "strftime('%H:00', first_ts/1000, 'unixepoch', 'localtime')", "Час"),
    (r"по раздел", "__group__", "Раздел"),
    (r"по тем|по папк|какие темы|частые темы|популярн|топ", "j.value", "Тема"),
    (r"по канал|по мессенджер", "provider", "Канал"),
    (r"по специалист|по оператор|по сотрудник|кто (больше|чаще)", "assignee", "Специалист"),
    (r"по минут|по длительност", "CAST(dur_min AS INTEGER) + 1", "Минута"),
    (r"по пометк|по манер|по характер", "mood", "Пометка"),
]


def _group_expr(expr: str) -> str:
    if expr != "__group__":
        return expr
    cases = " ".join(f"WHEN j.value IN ({','.join(repr(c['key']) for c in g['children'])}) THEN '{g['name']}'"
                     for g in folders.GROUPS)
    return f"CASE {cases} WHEN j.value = 'undef' THEN 'Не определённая тема' ELSE 'не в архиве' END"


def _label(col: str, v):
    if col == "Тема":
        return folders.name_of(v) or ("не в архиве" if not v else v)
    if col == "Пометка":
        return next((m["name"] for m in moods.MOODS if m["key"] == v), "без пометки")
    if col == "Канал":
        return {"telegram": "Telegram", "max": "MAX"}.get(v, v)
    if col == "Специалист":
        return v or "не назначен"
    if col == "Месяц" and v:
        y, mth = v.split("-")
        return f"{MONTH_NAMES[int(mth) - 1]} {y}"
    return v


def parse(question: str, now: dt.datetime | None = None) -> dict | None:
    """Вопрос -> план запроса. None, если вопрос не понят."""
    now = now or dt.datetime.now()
    q = " " + question.lower().replace("ё", "е") + " "

    # --- что считаем
    if re.search(r"средн\w* (врем|длительн|продолжительн)|сколько (в среднем )?длит", q):
        metric, what = "ROUND(AVG(dur_min), 1)", ("мин в среднем", "мин", "мин")
    elif re.search(r"пользовател|клиент|человек|людей|юзер|организац", q) and "сообщен" not in q:
        metric, what = "COUNT(DISTINCT contact_id)", ("пользователь", "пользователя", "пользователей")
    elif "сообщен" in q:
        metric, what = "SUM(n_in + n_out)", ("сообщение", "сообщения", "сообщений")
    elif re.search(r"обращен|вопрос|заявк|разговор|диалог|чат|тикет|сколько|топ|част|популяр|по тем", q):
        metric, what = "COUNT(*)", ("обращение", "обращения", "обращений")
    else:
        return None

    where, said = [], []

    per = _period(q, now)
    if per:
        where.append(f"first_ts >= {_ms(per[0])} AND first_ts < {_ms(per[1])}")
        said.append(per[2])

    dur = _duration(q)
    if dur:
        where.append(dur[0])
        said.append(dur[1])

    group = None
    for rx, expr, title in GROUP_BY:
        if re.search(rx, q):
            group = (_group_expr(expr), title)
            break

    topic_keys = [] if group and group[1] in ("Тема", "Раздел") else _topics(q)
    if topic_keys:
        sql, name = _topic_sql(topic_keys)
        where.append(sql)
        said.append("по теме " + name)

    for rx, key in MOOD_RULES:
        if re.search(rx, q):
            where.append(f"mood = '{key}'")
            said.append("клиенты с пометкой «" + next(m["name"] for m in moods.MOODS if m["key"] == key) + "»")
    if "telegram" in q or "телеграм" in q:
        where.append("provider = 'telegram'"); said.append("в Telegram")
    elif re.search(r"\bmax\b|\bмакс\b|\bмаксе\b", q):
        where.append("provider = 'max'"); said.append("в MAX")
    if re.search(r"без ключа|нет ключа", q):
        where.append("license = 'нет ключа'"); said.append("без ключа")
    if re.search(r"в архив|решен|закрыт", q) and "не в архив" not in q:
        where.append("status IN ('archived', 'closed')"); said.append("закрытые")
    elif re.search(r"без ответа|не ответил|неотвечен", q):
        where.append("n_out = 0"); said.append("без ответа специалиста")
    elif re.search(r"в работе|открыт|активн", q):
        where.append("status IN ('new', 'open', 'waiting')"); said.append("в работе")

    by_topic = bool(group and group[1] in ("Тема", "Раздел"))
    if by_topic:
        said.append("только архив")   # темы есть только у архивных
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    if by_topic:
        # Обращение может лежать в нескольких папках — разворачиваем его
        # в строку на каждую папку, но внутри одного раздела считаем один раз
        expr, title = group
        src = f"(SELECT DISTINCT conv.*, {expr} AS k FROM conv, json_each(conv.folders) j) conv"
        sql = f"{BASE} SELECT k, {metric} AS n FROM {src}{cond} GROUP BY k ORDER BY n DESC LIMIT 60"
    elif group:
        expr, title = group
        order = "n DESC" if title in ("Специалист", "Канал", "Пометка") else "k"
        sql = f"{BASE} SELECT {expr} AS k, {metric} AS n FROM conv{cond} GROUP BY k ORDER BY {order} LIMIT 60"
    else:
        sql = f"{BASE} SELECT {metric} AS n FROM conv{cond}"
    return {"sql": sql, "group": group, "what": what, "said": said}


def _answer_local(question: str) -> dict:
    plan = parse(question)
    if not plan:
        return {"ok": False, "engine": "local",
                "answer": "Не разобрал вопрос. Спросите, что посчитать: пользователей, "
                          "обращения, сообщения или среднюю длительность — и за какой период."}
    cols, rows = _run(plan["sql"])
    one, few, many = plan["what"]
    said = ", ".join(plan["said"])

    if plan["group"]:
        title = plan["group"][1]
        rows = [[_label(title, k), n or 0] for k, n in rows]
        # По темам одно обращение может попасть в несколько строк — сумма соврёт
        total = sum(r[1] for r in rows) if "AVG" not in plan["sql"] and title not in ("Тема", "Раздел") else None
        text = (f"{title}: {len(rows)} {_plural(len(rows), 'строка', 'строки', 'строк')}"
                + (f", всего {total:g} {_plural(total, one, few, many)}" if total is not None else "")
                + (f" · {said}" if said else ""))
        return {"ok": True, "engine": "local", "answer": text, "columns": [title, many.capitalize()],
                "rows": rows, "chart": "bar", "understood": said}

    n = rows[0][0] if rows and rows[0][0] is not None else 0
    unit = one if "AVG" in plan["sql"] else _plural(n, one, few, many)
    return {"ok": True, "engine": "local", "value": n, "unit": unit,
            "answer": f"{n:g} {unit}" + (f" — {said}" if said else ""), "understood": said}


# ---------------------------------------------------------------- Claude
def claude_ready() -> bool:
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "Один SELECT к витрине conv (без WITH conv — он подставится сам)"},
        "title": {"type": "string", "description": "Короткий заголовок ответа по-русски"},
        "shape": {"type": "string", "enum": ["number", "table", "bar"]},
        "unit": {"type": "string", "description": "Единица для числа: пользователей, обращений, мин…"},
        "understood": {"type": "string", "description": "Как понят вопрос, одной фразой"},
    },
    "required": ["sql", "title", "shape", "unit", "understood"],
    "additionalProperties": False,
}


def _answer_claude(question: str) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    today = dt.datetime.now().strftime("%Y-%m-%d %H:%M, %A")
    system = (
        "Ты превращаешь вопросы руководителя техподдержки в один SQL-запрос для SQLite.\n"
        + SCHEMA_FOR_MODEL + _folder_catalog()
        + "\nПиши только SELECT по витрине conv (или по сырым таблицам). Не пиши WITH conv — "
          "он уже подставлен. Для разбивок возвращай 2 колонки: подпись и число, "
          "подписи тем и пометок — человеческими названиями через CASE. "
          "Если вопрос про одно число — верни одну строку с одной колонкой."
    )
    resp = client.beta.messages.create(
        model=config.ASK_MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium",
                       "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
        system=system,
        messages=[{"role": "user", "content": f"Сейчас {today}.\nВопрос: {question}"}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("модель отказалась отвечать")
    text = next(b.text for b in resp.content if b.type == "text")
    plan = json.loads(text)

    sql = plan["sql"].strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|with)\b", sql) or ";" in sql:
        raise RuntimeError("модель вернула не SELECT")
    cols, rows = _run(BASE + sql)

    if plan["shape"] == "number" and rows and len(rows[0]) == 1:
        n = rows[0][0] if rows[0][0] is not None else 0
        return {"ok": True, "engine": "claude", "value": n, "unit": plan["unit"],
                "answer": f"{n:g} {plan['unit']}" if isinstance(n, (int, float)) else str(n),
                "title": plan["title"], "understood": plan["understood"]}
    chart = "bar" if plan["shape"] == "bar" and len(cols) == 2 else ""
    return {"ok": True, "engine": "claude", "answer": plan["title"], "title": plan["title"],
            "columns": cols, "rows": rows, "chart": chart, "understood": plan["understood"]}


# ---------------------------------------------------------------- вход
def ask(question: str) -> dict:
    question = (question or "").strip()[:500]
    if not question:
        return {"ok": False, "answer": "Задайте вопрос — например: сколько пользователей "
                                       "написало про драйвер в сентябре?"}
    if claude_ready():
        try:
            return _answer_claude(question)
        except Exception as e:  # noqa: BLE001 — есть запасной движок
            log.warning("Claude не ответил (%s), беру встроенный разбор", e)
    try:
        return _answer_local(question)
    except sqlite3.Error as e:
        return {"ok": False, "engine": "local", "answer": f"Запрос не выполнился: {e}"}


# ---------------------------------------------------------------- сводка дня
def today() -> dict:
    """Цифры за сегодня для тихого экрана и типичный день для сравнения.

    Оценку «напряжённый ли день» ставит интерфейс: ему нужны сырые числа
    и то, сколько обращений обычно приходит за день.
    """
    now = dt.datetime.now()
    day0 = _ms(now.replace(hour=0, minute=0, second=0, microsecond=0))
    q = lambda sql, args=(): _run(sql, args)[1]

    new_convs = q(BASE + "SELECT COUNT(*) FROM conv WHERE first_ts >= ?", (day0,))[0][0]
    clients = q("SELECT COUNT(DISTINCT c.contact_id) FROM messages m JOIN conversations c "
                "ON c.id = m.conversation_id WHERE m.direction = 'in' AND m.ts >= ?", (day0,))[0][0]
    n_in, n_out = q("SELECT SUM(direction = 'in'), SUM(direction = 'out') FROM messages "
                    "WHERE ts >= ?", (day0,))[0]
    archived = q("SELECT COUNT(*) FROM conversations WHERE status = 'archived' AND archived_at >= ?",
                 (day0,))[0][0]
    waiting = q(BASE + "SELECT COUNT(*) FROM conv WHERE status NOT IN ('archived', 'closed') AND "
                "(SELECT direction FROM messages m WHERE m.conversation_id = conv.id "
                " AND m.direction != 'sys' ORDER BY m.ts DESC LIMIT 1) = 'in'")[0][0]

    # Скорость ответа: от первого сообщения клиента в «серии» до ответа специалиста
    reply = q(
        """SELECT AVG(r - i.ts) / 60000.0 FROM (
             SELECT i.*, (SELECT MIN(o.ts) FROM messages o WHERE o.conversation_id = i.conversation_id
                            AND o.direction = 'out' AND o.ts > i.ts) AS r
             FROM messages i
             WHERE i.direction = 'in' AND i.ts >= ?
               AND NOT EXISTS (SELECT 1 FROM messages p WHERE p.conversation_id = i.conversation_id
                                 AND p.direction = 'in' AND p.ts < i.ts
                                 AND p.ts > COALESCE((SELECT MAX(o2.ts) FROM messages o2
                                                      WHERE o2.conversation_id = i.conversation_id
                                                        AND o2.direction = 'out' AND o2.ts < i.ts), 0))
           ) i WHERE r IS NOT NULL""", (day0,))[0][0]

    busiest = q("SELECT strftime('%H', ts/1000, 'unixepoch', 'localtime') AS h, COUNT(*) AS n "
                "FROM messages WHERE direction = 'in' AND ts >= ? GROUP BY h ORDER BY n DESC LIMIT 1",
                (day0,))

    # Обычный день — среднее по дням с обращениями за прошлые две недели
    base_rows = q(BASE + "SELECT date(first_ts/1000, 'unixepoch', 'localtime') AS d, COUNT(*) "
                  "FROM conv WHERE first_ts >= ? AND first_ts < ? GROUP BY d",
                  (day0 - 14 * 86_400_000, day0))
    typical = round(sum(r[1] for r in base_rows) / len(base_rows), 1) if base_rows else None

    return {
        "new_convs": new_convs or 0, "clients": clients or 0,
        "in_msgs": n_in or 0, "out_msgs": n_out or 0,
        "archived": archived or 0, "waiting": waiting or 0,
        "avg_reply_min": round(reply, 1) if reply is not None else None,
        "busiest_hour": int(busiest[0][0]) if busiest else None,
        "typical": typical, "typical_days": len(base_rows),
    }

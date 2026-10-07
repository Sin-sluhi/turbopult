"""Умный поиск по архиву.

Русский язык склоняет всё подряд, поэтому сравниваем не слова целиком,
а основы: «ключа», «ключом», «ключи» дают одну основу «ключ». Плюс
словарь синонимов предметной области — по запросу «ключ» находятся и
обращения про HASP, лицензию и активацию.

Для нескольких тысяч обращений этого достаточно. Когда архив вырастет,
то же самое переносится на FTS5 без изменения интерфейса функции.
"""
from __future__ import annotations

import re

from . import db, folders

SYNONYMS = {
    "ключ":      ["hasp", "лицензи", "активац", "sentinel", "защит"],
    "лицензи":   ["ключ", "hasp", "активац"],
    "арпс":      ["xml", "выгруз", "обмен", "формат"],
    "выгруз":    ["арпс", "xml", "экспорт", "обмен"],
    "кс":        ["акт", "ведомост", "накопительн"],
    "акт":       ["кс", "ведомост"],
    "тсн":       ["территориальн", "норматив", "индекс"],
    "фснб":      ["фер", "гэсн", "база", "дополнен", "норматив"],
    "обновлен":  ["дополнен", "сборк", "верси"],
    "счет":      ["оплат", "реквизит", "кп", "предложен"],
    "обучен":    ["вебинар", "курс", "методичк"],
    "устан":     ["настройк", "развертыван", "служб"],
}

STOP = {"и", "в", "на", "не", "с", "по", "а", "что", "как", "для", "от", "до",
        "за", "у", "о", "об", "к", "из", "же", "ли", "бы", "или", "это", "при",
        "мне", "мы", "вы", "он", "она", "они", "есть", "был", "было", "быть"}

_ENDINGS = re.compile(
    r"(ами|ями|ость|ений|ения|ение|ыми|ими|ого|его|ому|ему|ая|ое|ые|ый|ой|ий|"
    r"ем|ом|ах|ях|ам|ям|ов|ев|ью|ия|ие|у|ю|а|я|ы|и|е|ь)$"
)
_WORDS = re.compile(r"[^a-zA-Zа-яА-ЯёЁ0-9-]+")


def stem(word: str) -> str:
    word = word.lower().replace("ё", "е")
    word = re.sub(r"[^a-zа-я0-9-]", "", word)
    if len(word) <= 4:
        return word
    return _ENDINGS.sub("", word)[:9]


def tokenize(text: str) -> list[str]:
    out = []
    for raw in _WORDS.split(text or ""):
        low = raw.lower()
        if len(low) > 1 and low not in STOP:
            s = stem(low)
            if s:
                out.append(s)
    return out


def expand(tokens: list[str]) -> dict[str, float]:
    """Основы запроса + синонимы с меньшим весом."""
    terms: dict[str, float] = {}
    for t in tokens:
        terms[t] = 1.0
        for key, extra in SYNONYMS.items():
            if t.startswith(key) or key.startswith(t):
                for s in extra:
                    terms.setdefault(s, 0.55)
    return terms


def _hits(word: str, term: str) -> bool:
    """Совпадение основ.

    Короткое слово не должно совпадать с длинным термином только по первой
    букве: иначе предлог «в» считается совпадением с «видит» и раздувает
    оценку. Префикс засчитываем от четырёх символов.
    """
    if word == term:
        return True
    if len(word) < 4 or len(term) < 4:
        return False
    return word.startswith(term) or term.startswith(word)


def _matches(words: list[str], term: str) -> int:
    return sum(1 for w in words if _hits(w, term))


def search(raw_query: str, limit: int = 40) -> list[dict]:
    base = tokenize(raw_query)
    if not base:
        return []
    terms = expand(base)

    rows = db.query(
        """SELECT c.id, c.topic, c.tags, c.folder, c.folders, c.last_ts,
                  k.display_name, k.org, k.license
           FROM conversations c
           JOIN contacts k ON k.id = c.contact_id
           WHERE c.status = 'archived'"""
    )

    folder_names = {f["key"]: f["name"] for f in folders.ALL}
    results = []

    for row in rows:
        texts = db.query(
            "SELECT text FROM messages WHERE conversation_id = ? AND direction != 'sys'",
            (row["id"],),
        )
        body = " ".join(t["text"] for t in texts)

        fields = [
            (row["topic"], 3.0),
            (row["org"] or "", 1.5),
            (row["display_name"] or "", 1.5),
            (" ".join(db.loads(row["tags"], [])), 2.5),
            (" ".join(folder_names.get(k, "") for k in db.loads(row["folders"], []) or [row["folder"]]), 2.0),
            (body, 1.0),
        ]
        tokenized = [(tokenize(text), weight) for text, weight in fields]

        score = 0.0
        hit = set()
        for term, term_weight in terms.items():
            found = False
            for words, field_weight in tokenized:
                n = _matches(words, term)
                if n:
                    score += n * field_weight * term_weight
                    found = True
            if found and term_weight == 1.0:
                hit.add(term)

        if not score:
            continue

        # Нашлись все слова запроса — почти наверняка именно этот случай
        if len(hit) == len(set(base)) and len(base) > 1:
            score *= 2.2

        results.append({
            "conversation_id": row["id"],
            "name": row["display_name"],
            "org": row["org"],
            "topic": row["topic"],
            "folder": row["folder"],
            "folder_name": folder_names.get(row["folder"], ""),
            "last_ts": row["last_ts"],
            "score": round(score, 2),
            "matched": sorted(hit),
            "snippet": _snippet(row["id"], terms),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def _snippet(conv_id: int, terms: dict[str, float]) -> str:
    """Сообщение, в котором совпало больше всего слов запроса."""
    rows = db.query(
        "SELECT text FROM messages WHERE conversation_id = ? AND direction != 'sys' AND text != ''",
        (conv_id,),
    )
    best, best_n = "", -1
    for r in rows:
        words = tokenize(r["text"])
        n = sum(1 for t in terms if _matches(words, t))
        if n > best_n:
            best, best_n = r["text"], n
    return best[:400]

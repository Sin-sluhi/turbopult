"""Папки архива: разделы-родители и темы внутри них.

Обращение кладётся в тему (дочернюю папку), раздел нужен для обзора:
«сколько всего было по нормативным базам» считается сложением его тем.
Порядок важен — он же порядок в интерфейсе. UNDEF всегда последняя.
"""

GROUPS = [
    {"key": "g_setup", "name": "Установка и подключение", "color": "#2f7fc1",
     "hint": "Поставить, скачать, подключиться",
     "children": [
         {"key": "install",   "name": "Установка"},
         {"key": "download",  "name": "Скачать"},
         {"key": "driver",    "name": "Драйвер"},
         {"key": "remote",    "name": "Удалённое подключение"},
     ]},
    {"key": "g_errors", "name": "Ошибки и сбои", "color": "#d0524a",
     "hint": "Программа падает или ведёт себя странно",
     "children": [
         {"key": "err_turbo", "name": "Ошибка в Турбосметчике"},
         {"key": "err_build", "name": "Ошибка в сборке"},
     ]},
    {"key": "g_work", "name": "Работа в программе", "color": "#7b5cd6",
     "hint": "Функции, документы, печать",
     "children": [
         {"key": "functions",  "name": "Функции программы"},
         {"key": "docprops",   "name": "Свойства документа"},
         {"key": "formulas",   "name": "Формулы и итоговые начисления"},
         {"key": "correction", "name": "Поправка"},
         {"key": "print",      "name": "Настройки печати"},
         {"key": "objsmeta",   "name": "Объектный сметчик"},
         {"key": "explnote",   "name": "Пояснительная записка"},
     ]},
    {"key": "g_norms", "name": "Нормативные базы", "color": "#c88a12",
     "hint": "Базы, дополнения, индексы",
     "children": [
         {"key": "base_q",   "name": "Вопрос по базе"},
         {"key": "addon",    "name": "Выбрать дополнение"},
         {"key": "snb",      "name": "Преобразовать СНБ"},
         {"key": "indexes",  "name": "Вопрос по индексам"},
         {"key": "techpart", "name": "Тех часть"},
         {"key": "pir",      "name": "ПИР"},
         {"key": "rzd",      "name": "РЖД"},
     ]},
    {"key": "g_exchange", "name": "Обмен и форматы", "color": "#12907f",
     "hint": "GGE/MGE, АРПС, импорт",
     "children": [
         {"key": "gge",      "name": "GGE/MGE импорт и экспорт"},
         {"key": "gge_exp",  "name": "Вопрос mge/gge от эксперта"},
         {"key": "arps",     "name": "АРПС"},
         {"key": "vor",      "name": "ВОР"},
         {"key": "pricelist", "name": "Импорт прайс-листа"},
     ]},
    {"key": "g_method", "name": "Методика и консультации", "color": "#3f9a52",
     "hint": "Как правильно посчитать",
     "children": [
         {"key": "method",   "name": "Вопрос по методике"},
         {"key": "consult",  "name": "Консультация"},
         {"key": "market",   "name": "Конъюнктурный анализ"},
         {"key": "nmck",     "name": "НМЦК, смета контракта"},
         {"key": "finderr",  "name": "Поиск ошибки в смете"},
     ]},
]

UNDEF = {"key": "undef", "name": "Не определённая тема",
         "hint": "Разберём позже и разложим по папкам"}

# Ключи прежнего плоского списка — чтобы старые обращения не потерялись
LEGACY = {
    "tsn": "base_q", "fsnb": "base_q", "license": "driver", "update": "download",
    "exchange": "gge", "acts": "functions", "billing": "consult",
    "study": "consult", "setup": "install",
}

ALL = [dict(c, group=g["key"]) for g in GROUPS for c in g["children"]] + [UNDEF]
KEYS = {f["key"] for f in ALL}
NAMES = {f["key"]: f["name"] for f in ALL}
GROUP_OF = {c["key"]: g for g in GROUPS for c in g["children"]}


def is_valid(key: str) -> bool:
    return key in KEYS


def name_of(key: str) -> str:
    return NAMES.get(key, "")

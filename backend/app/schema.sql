PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS contacts (
  id               INTEGER PRIMARY KEY,
  provider         TEXT    NOT NULL,
  external_user_id TEXT    NOT NULL,
  display_name     TEXT    NOT NULL DEFAULT '',
  username         TEXT    NOT NULL DEFAULT '',
  phone            TEXT    NOT NULL DEFAULT '',
  org              TEXT    NOT NULL DEFAULT '',
  license          TEXT    NOT NULL DEFAULT '',
  product          TEXT    NOT NULL DEFAULT '',
  created_at       INTEGER NOT NULL,
  UNIQUE (provider, external_user_id)
);

CREATE TABLE IF NOT EXISTS conversations (
  id               INTEGER PRIMARY KEY,
  contact_id       INTEGER NOT NULL REFERENCES contacts(id),
  provider         TEXT    NOT NULL,
  external_chat_id TEXT    NOT NULL,
  status           TEXT    NOT NULL DEFAULT 'new',
  assignee         TEXT    NOT NULL DEFAULT '',
  topic            TEXT    NOT NULL DEFAULT '',
  tags             TEXT    NOT NULL DEFAULT '[]',
  note             TEXT    NOT NULL DEFAULT '',
  unread           INTEGER NOT NULL DEFAULT 0,
  last_ts          INTEGER NOT NULL,
  created_at       INTEGER NOT NULL,
  UNIQUE (provider, external_chat_id)
);

CREATE TABLE IF NOT EXISTS messages (
  id                  INTEGER PRIMARY KEY,
  conversation_id     INTEGER NOT NULL REFERENCES conversations(id),
  direction           TEXT    NOT NULL,             -- in | out | sys
  text                TEXT    NOT NULL DEFAULT '',
  attachments         TEXT    NOT NULL DEFAULT '[]',
  external_message_id TEXT    NOT NULL DEFAULT '',
  delivery            TEXT    NOT NULL DEFAULT 'sent',
  author              TEXT    NOT NULL DEFAULT '',
  ts                  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, ts);
CREATE INDEX IF NOT EXISTS idx_conv_last ON conversations(last_ts DESC);

-- MAX повторяет неудачную доставку до 10 раз, Telegram тоже умеет дублировать.
-- Без этой таблицы один вопрос клиента превратится в несколько сообщений.
CREATE TABLE IF NOT EXISTS seen_updates (
  key TEXT PRIMARY KEY,
  ts  INTEGER NOT NULL
);

-- Папка архива и присутствие клиента добавляются миграцией в db.connect(),
-- здесь описаны для новых баз.

-- Сервисная форма: итог разговора. Копия уходит в Google Таблицу,
-- sent = 1 — таблица приняла строку.
CREATE TABLE IF NOT EXISTS service_forms (
  id           INTEGER PRIMARY KEY,
  date         TEXT    NOT NULL,
  time         TEXT    NOT NULL,
  created_at   INTEGER NOT NULL,
  author       TEXT    NOT NULL DEFAULT '',
  employee     TEXT    NOT NULL DEFAULT '',
  serial       TEXT    NOT NULL DEFAULT '',
  workplace    TEXT    NOT NULL DEFAULT '',
  organization TEXT    NOT NULL DEFAULT '',
  subscription TEXT    NOT NULL DEFAULT '',
  phone        TEXT    NOT NULL DEFAULT '',
  name         TEXT    NOT NULL DEFAULT '',
  comment      TEXT    NOT NULL DEFAULT '',
  questions    TEXT    NOT NULL DEFAULT '',
  sent         INTEGER NOT NULL DEFAULT 0,
  sent_at      INTEGER NOT NULL DEFAULT 0,
  error        TEXT    NOT NULL DEFAULT ''
);

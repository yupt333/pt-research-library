"""SQLite connection and schema initialization."""

import sqlite3
from pathlib import Path
from typing import Union


DatabasePath = Union[str, Path]


SCHEMA_SQL = """
BEGIN;

CREATE TABLE IF NOT EXISTS literature (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    authors TEXT,
    journal TEXT,
    publication_year INTEGER,
    volume TEXT,
    issue TEXT,
    pages TEXT,
    doi TEXT,
    pmid TEXT,
    url TEXT,
    language TEXT,
    publication_type TEXT,
    abstract TEXT,
    pdf_path TEXT,
    personal_summary TEXT,
    ai_summary TEXT,
    ai_summary_status TEXT NOT NULL DEFAULT '未作成'
        CHECK (ai_summary_status IN ('未作成', '未確認', '確認済み', '修正済み')),
    general_note TEXT,
    key_findings TEXT,
    methods_note TEXT,
    clinical_note TEXT,
    limitation_note TEXT,
    relevance_note TEXT,
    evidence_level TEXT,
    verification_status TEXT NOT NULL DEFAULT '未確認'
        CHECK (verification_status IN ('未確認', '一部確認', '確認済み', '要確認')),
    adoption_status TEXT NOT NULL DEFAULT '未判定'
        CHECK (adoption_status IN ('未判定', '採用候補', '採用', '除外')),
    exclusion_reason TEXT,
    rating INTEGER DEFAULT NULL
        CHECK (
            rating IS NULL
            OR (typeof(rating) = 'integer' AND rating BETWEEN 1 AND 5)
        ),
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE
        CHECK (length(trim(name)) > 0)
);

CREATE TABLE IF NOT EXISTS literature_tags (
    literature_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (literature_id, tag_id),
    FOREIGN KEY (literature_id) REFERENCES literature(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS usage_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    literature_id INTEGER NOT NULL,
    usage_type TEXT,
    project_name TEXT,
    usage_note TEXT,
    used_at TEXT,
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (literature_id) REFERENCES literature(id) ON DELETE CASCADE
);

COMMIT;
"""


def connect_database(database_path: DatabasePath) -> sqlite3.Connection:
    """Open a SQLite connection with foreign-key enforcement enabled."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path: DatabasePath) -> None:
    """Create the Phase 1 tables without removing or replacing existing data."""
    connection = connect_database(database_path)
    try:
        connection.executescript(SCHEMA_SQL)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

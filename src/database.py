"""SQLite connection, schema initialization, and additive migrations."""

import sqlite3
from pathlib import Path
from typing import Union

from src.backup import create_database_backup


DatabasePath = Union[str, Path]

CURRENT_SCHEMA_VERSION = 2

_PHASE1_TABLE_NAMES = frozenset(
    {"literature", "tags", "literature_tags", "usage_history"}
)
_STRUCTURED_TABLE_NAMES = frozenset(
    {
        "schema_migrations",
        "structured_entities",
        "structured_fields",
        "evidence_references",
        "structured_field_evidence",
        "structured_entity_evidence",
    }
)
_VERSION1_TABLE_NAMES = _PHASE1_TABLE_NAMES | _STRUCTURED_TABLE_NAMES
_PROJECT_TABLE_NAMES = frozenset(
    {
        "research_projects",
        "research_project_literature",
        "research_project_items",
    }
)
_CURRENT_TABLE_NAMES = _VERSION1_TABLE_NAMES | _PROJECT_TABLE_NAMES


class DatabaseSchemaError(RuntimeError):
    """Raised when an existing database cannot be migrated safely."""


_PHASE1_TABLES_SQL = """
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
"""


_STRUCTURED_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS structured_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    literature_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL
        CHECK (
            entity_type IN (
                'study',
                'method_measurement_imaging',
                'method_body_condition',
                'method_task_protocol',
                'method_analysis',
                'method_validation_statistics',
                'outcome',
                'result',
                'limitation',
                'concept',
                'research_relevance'
            )
        ),
    parent_entity_id INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(sort_order) = 'integer' AND sort_order >= 0),
    verification TEXT NOT NULL
        CHECK (verification IN ('ai_unverified', 'user_verified')),
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, literature_id),
    CHECK (entity_type != 'result' OR parent_entity_id IS NOT NULL),
    FOREIGN KEY (literature_id)
        REFERENCES literature(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_entity_id, literature_id)
        REFERENCES structured_entities(id, literature_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS structured_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    literature_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    field_key TEXT NOT NULL CHECK (length(trim(field_key)) > 0),
    content_role TEXT NOT NULL
        CHECK (content_role IN ('source_fact', 'interpretation')),
    value_json TEXT,
    availability TEXT
        CHECK (
            availability IS NULL
            OR availability IN (
                'reported',
                'not_reported',
                'not_extracted',
                'unclear',
                'not_applicable'
            )
        ),
    verification TEXT NOT NULL
        CHECK (verification IN ('ai_unverified', 'user_verified')),
    note TEXT,
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (entity_id, field_key),
    UNIQUE (id, literature_id),
    CHECK (
        (
            content_role = 'source_fact'
            AND availability IS NOT NULL
            AND (
                (availability = 'reported' AND value_json IS NOT NULL)
                OR
                (availability != 'reported' AND value_json IS NULL)
            )
        )
        OR
        (
            content_role = 'interpretation'
            AND availability IS NULL
            AND value_json IS NOT NULL
        )
    ),
    FOREIGN KEY (literature_id)
        REFERENCES literature(id) ON DELETE CASCADE,
    FOREIGN KEY (entity_id, literature_id)
        REFERENCES structured_entities(id, literature_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evidence_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    literature_id INTEGER NOT NULL,
    pdf_page INTEGER
        CHECK (
            pdf_page IS NULL
            OR (typeof(pdf_page) = 'integer' AND pdf_page > 0)
        ),
    printed_page TEXT,
    section TEXT,
    subsection TEXT,
    table_label TEXT,
    figure_label TEXT,
    quote_text TEXT,
    note TEXT,
    verification TEXT NOT NULL
        CHECK (verification IN ('ai_unverified', 'user_verified')),
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, literature_id),
    CHECK (
        pdf_page IS NOT NULL
        OR length(
            trim(COALESCE(printed_page, ''), ' ' || char(9) || char(10) || char(13))
        ) > 0
        OR length(
            trim(COALESCE(section, ''), ' ' || char(9) || char(10) || char(13))
        ) > 0
        OR length(
            trim(COALESCE(subsection, ''), ' ' || char(9) || char(10) || char(13))
        ) > 0
        OR length(
            trim(COALESCE(table_label, ''), ' ' || char(9) || char(10) || char(13))
        ) > 0
        OR length(
            trim(COALESCE(figure_label, ''), ' ' || char(9) || char(10) || char(13))
        ) > 0
        OR length(
            trim(COALESCE(quote_text, ''), ' ' || char(9) || char(10) || char(13))
        ) > 0
    ),
    FOREIGN KEY (literature_id)
        REFERENCES literature(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS structured_field_evidence (
    literature_id INTEGER NOT NULL,
    field_id INTEGER NOT NULL,
    evidence_id INTEGER NOT NULL,
    PRIMARY KEY (field_id, evidence_id),
    FOREIGN KEY (literature_id)
        REFERENCES literature(id) ON DELETE CASCADE,
    FOREIGN KEY (field_id, literature_id)
        REFERENCES structured_fields(id, literature_id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_id, literature_id)
        REFERENCES evidence_references(id, literature_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS structured_entity_evidence (
    literature_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    evidence_id INTEGER NOT NULL,
    PRIMARY KEY (entity_id, evidence_id),
    FOREIGN KEY (literature_id)
        REFERENCES literature(id) ON DELETE CASCADE,
    FOREIGN KEY (entity_id, literature_id)
        REFERENCES structured_entities(id, literature_id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_id, literature_id)
        REFERENCES evidence_references(id, literature_id) ON DELETE CASCADE
);
"""


_PROJECT_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS research_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE
        CHECK (length(trim(name)) > 0),
    objective TEXT,
    current_status TEXT,
    protocol_note TEXT,
    general_note TEXT,
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS research_project_literature (
    project_id INTEGER NOT NULL,
    literature_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (project_id, literature_id),
    FOREIGN KEY (project_id)
        REFERENCES research_projects(id) ON DELETE CASCADE,
    FOREIGN KEY (literature_id)
        REFERENCES literature(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS research_project_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    item_type TEXT NOT NULL
        CHECK (
            item_type IN (
                'concept',
                'unresolved_question',
                'next_action'
            )
        ),
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    note TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(sort_order) = 'integer' AND sort_order >= 0),
    created_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL
        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (project_id)
        REFERENCES research_projects(id) ON DELETE CASCADE
);
"""


SCHEMA_SQL = f"""
BEGIN;

{_PHASE1_TABLES_SQL}

{_STRUCTURED_TABLES_SQL}

INSERT INTO schema_migrations (version) VALUES (1);

{_PROJECT_TABLES_SQL}

INSERT INTO schema_migrations (version) VALUES (2);

COMMIT;
"""


MIGRATION_1_SQL = f"""
{_STRUCTURED_TABLES_SQL}

INSERT INTO schema_migrations (version) VALUES (1);
"""


MIGRATION_2_SQL = f"""
{_PROJECT_TABLES_SQL}

INSERT INTO schema_migrations (version) VALUES (2);
"""


def connect_database(database_path: DatabasePath) -> sqlite3.Connection:
    """Open a SQLite connection with foreign-key enforcement enabled."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _user_table_names(connection: sqlite3.Connection) -> frozenset[str]:
    """Return non-internal table names without changing database state."""
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    return frozenset(row["name"] for row in rows)


def _migration_level(
    connection: sqlite3.Connection,
    table_names: frozenset[str],
) -> int:
    """Return the highest recorded structured-schema migration version."""
    if "schema_migrations" not in table_names:
        return 0

    level = connection.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()[0]
    if level is None:
        return 0
    if isinstance(level, bool) or not isinstance(level, int) or level < 0:
        raise DatabaseSchemaError(
            "schema_migrationsのversionが有効な非負整数ではありません。"
        )
    return level


def _require_tables(
    table_names: frozenset[str],
    required_names: frozenset[str],
    *,
    database_kind: str,
) -> None:
    """Raise a clear inconsistency error when required tables are missing."""
    missing_names = sorted(required_names - table_names)
    if missing_names:
        missing_text = ", ".join(missing_names)
        raise DatabaseSchemaError(
            f"{database_kind} databaseに必須tableがありません: {missing_text}"
        )


def _require_migration_history(
    connection: sqlite3.Connection,
    expected_versions: tuple[int, ...],
    *,
    database_kind: str,
) -> None:
    """Require complete ordered migration history, not only its maximum."""
    versions = tuple(
        row[0]
        for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    )
    if versions != expected_versions:
        raise DatabaseSchemaError(
            f"{database_kind} databaseのschema_migrations履歴が不正です: "
            f"expected {expected_versions!r}, found {versions!r}"
        )


def _rollback_without_hiding_error(connection: sqlite3.Connection) -> None:
    """Best-effort rollback used while preserving an original exception."""
    try:
        connection.rollback()
    except BaseException:
        pass


def _create_fresh_database(connection: sqlite3.Connection) -> None:
    """Create the complete current schema atomically."""
    try:
        connection.executescript(SCHEMA_SQL)
    except BaseException:
        _rollback_without_hiding_error(connection)
        raise


def _apply_migration_1(connection: sqlite3.Connection) -> None:
    """Add the Phase 2 structured schema in one rollback-safe transaction."""
    try:
        connection.executescript(f"BEGIN IMMEDIATE;\n{MIGRATION_1_SQL}")
        table_names = _user_table_names(connection)
        _require_tables(
            table_names,
            _VERSION1_TABLE_NAMES,
            database_kind="migration 1",
        )
        if _migration_level(connection, table_names) != 1:
            raise DatabaseSchemaError(
                "migration 1のversion metadataを確認できませんでした。"
            )
        _require_migration_history(
            connection, (1,), database_kind="migration 1"
        )
        connection.commit()
    except BaseException:
        _rollback_without_hiding_error(connection)
        raise


def _apply_migration_2(connection: sqlite3.Connection) -> None:
    """Add the Phase 2-9 project schema in one rollback-safe transaction."""
    try:
        connection.executescript(f"BEGIN IMMEDIATE;\n{MIGRATION_2_SQL}")
        table_names = _user_table_names(connection)
        _require_tables(
            table_names,
            _CURRENT_TABLE_NAMES,
            database_kind="migration 2",
        )
        if _migration_level(connection, table_names) != CURRENT_SCHEMA_VERSION:
            raise DatabaseSchemaError(
                "migration 2のversion metadataを確認できませんでした。"
            )
        _require_migration_history(
            connection, (1, 2), database_kind="migration 2"
        )
        connection.commit()
    except BaseException:
        _rollback_without_hiding_error(connection)
        raise


def initialize_database(
    database_path: DatabasePath,
    *,
    migration_backup_directory: object = None,
) -> None:
    """Create or safely migrate a database to the current additive schema."""
    connection = connect_database(database_path)
    try:
        table_names = _user_table_names(connection)
        if not table_names:
            _create_fresh_database(connection)
            return

        migration_level = _migration_level(connection, table_names)
        if migration_level > CURRENT_SCHEMA_VERSION:
            raise DatabaseSchemaError(
                "database schema version "
                f"{migration_level} is newer than supported version "
                f"{CURRENT_SCHEMA_VERSION}; downgrade is not allowed."
            )

        if migration_level == CURRENT_SCHEMA_VERSION:
            _require_tables(
                table_names,
                _CURRENT_TABLE_NAMES,
                database_kind="current",
            )
            _require_migration_history(
                connection, (1, 2), database_kind="current"
            )
            return

        if migration_level == 1:
            _require_tables(
                table_names,
                _VERSION1_TABLE_NAMES,
                database_kind="version 1",
            )
            _require_migration_history(
                connection, (1,), database_kind="version 1"
            )
            unexpected_project_tables = sorted(
                table_names & _PROJECT_TABLE_NAMES
            )
            if unexpected_project_tables:
                raise DatabaseSchemaError(
                    "version 1 databaseにversion 2 project tableが"
                    "部分的に存在します: "
                    + ", ".join(unexpected_project_tables)
                )
        else:
            _require_tables(
                table_names,
                _PHASE1_TABLE_NAMES,
                database_kind="legacy Phase 1",
            )
            unexpected_migration_tables = sorted(
                table_names
                & (_STRUCTURED_TABLE_NAMES | _PROJECT_TABLE_NAMES)
            )
            if unexpected_migration_tables:
                raise DatabaseSchemaError(
                    "legacy Phase 1 databaseにmigration metadataまたは"
                    "追加tableが部分的に存在します: "
                    + ", ".join(unexpected_migration_tables)
                )

        if migration_backup_directory is None:
            raise DatabaseSchemaError(
                "database migration requires "
                "migration_backup_directory; schema was not changed."
            )

        create_database_backup(connection, migration_backup_directory)
        if migration_level == 0:
            _apply_migration_1(connection)
        _apply_migration_2(connection)
    finally:
        connection.close()

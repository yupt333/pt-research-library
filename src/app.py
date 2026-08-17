"""Application startup for the PT Research Library CLI."""

from pathlib import Path

from src.cli import run_cli
from src.database import connect_database, initialize_database


_DATABASE_FILENAME = "pt_research_library.sqlite3"


def get_project_root() -> Path:
    """Return the repository root derived from this module's location."""
    return Path(__file__).resolve().parent.parent


def run_application(project_root: str | Path) -> None:
    """Prepare local storage and run the CLI for one application session."""
    root = Path(project_root)
    data_directory = root / "data"
    export_directory = root / "exports"
    backup_directory = root / "backups"

    for directory in (data_directory, export_directory, backup_directory):
        directory.mkdir(exist_ok=True)

    database_path = data_directory / _DATABASE_FILENAME
    initialize_database(database_path)
    connection = connect_database(database_path)
    try:
        run_cli(
            connection,
            export_directory=export_directory,
            backup_directory=backup_directory,
        )
    finally:
        connection.close()


def main() -> None:
    """Run the application using paths rooted at the repository."""
    run_application(get_project_root())

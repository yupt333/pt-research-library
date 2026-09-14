"""Read-only navigation from saved Evidence to a local original PDF."""

from __future__ import annotations

import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.repository import get_literature
from src.structured_repository import get_evidence_reference


PDF_PATH_UNREGISTERED_MESSAGE = (
    "pdf_path未登録です。文献編集でpdf_pathを登録してください。"
)
PDF_FILE_NOT_FOUND_MESSAGE = "PDFファイルが見つかりません。"
PDF_PATH_DIRECTORY_MESSAGE = "pdf_pathがファイルではなくdirectoryを示しています。"
PDF_PATH_NOT_PDF_MESSAGE = "pdf_pathがPDFファイルを示していません。"
PDF_PATH_VALIDATION_MESSAGE = "pdf_pathを安全に確認できません。"
PDF_OPEN_FAILURE_MESSAGE = "原著PDFを開けませんでした。"


class PdfNavigationError(ValueError):
    """Raised when Literature and Evidence cannot form a safe target."""


@dataclass(frozen=True)
class PdfNavigationTarget:
    """An immutable Evidence-to-PDF navigation preview."""

    literature_id: int
    literature_title: str
    stored_pdf_path: Optional[str]
    resolved_pdf_path: Optional[Path]
    evidence_id: int
    pdf_page: Optional[int]
    printed_page: Optional[str]
    section: Optional[str]
    subsection: Optional[str]
    table_label: Optional[str]
    figure_label: Optional[str]
    verification: str
    path_error: Optional[str] = None

    @property
    def can_open(self) -> bool:
        """Return whether local path validation found an openable PDF."""
        return self.resolved_pdf_path is not None and self.path_error is None


@dataclass(frozen=True)
class PdfOpenResult:
    """The safe outcome of one explicitly requested PDF open attempt."""

    success: bool
    target: PdfNavigationTarget
    error_message: Optional[str] = None


def get_default_project_root() -> Path:
    """Return the application project root without consulting the cwd."""
    return Path(__file__).resolve().parent.parent


def _require_positive_id(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PdfNavigationError(f"{name}は1以上の整数で指定してください。")
    return value


def _resolve_project_root(project_root: object | None) -> Path:
    root = (
        get_default_project_root()
        if project_root is None
        else Path(project_root).expanduser()
    )
    return root.resolve(strict=False)


def _resolve_and_validate_pdf_path(
    stored_pdf_path: Optional[str],
    project_root: object | None,
) -> tuple[Optional[Path], Optional[str]]:
    if stored_pdf_path is None or not stored_pdf_path.strip():
        return None, PDF_PATH_UNREGISTERED_MESSAGE

    try:
        stored_path = Path(stored_pdf_path).expanduser()
        if not stored_path.is_absolute():
            stored_path = _resolve_project_root(project_root) / stored_path
        resolved_path = stored_path.resolve(strict=True)
    except FileNotFoundError:
        return None, PDF_FILE_NOT_FOUND_MESSAGE
    except (OSError, RuntimeError, ValueError):
        return None, PDF_PATH_VALIDATION_MESSAGE

    if resolved_path.is_dir():
        return resolved_path, PDF_PATH_DIRECTORY_MESSAGE
    if not resolved_path.is_file():
        return resolved_path, PDF_PATH_VALIDATION_MESSAGE
    if resolved_path.suffix.lower() != ".pdf":
        return resolved_path, PDF_PATH_NOT_PDF_MESSAGE
    return resolved_path, None


def build_navigation_target(
    connection: sqlite3.Connection,
    literature_id: object,
    evidence_id: object,
    *,
    project_root: object | None = None,
) -> PdfNavigationTarget:
    """Build a current, ownership-checked local PDF navigation target."""
    checked_literature_id = _require_positive_id("literature_id", literature_id)
    checked_evidence_id = _require_positive_id("evidence_id", evidence_id)

    literature = get_literature(connection, checked_literature_id)
    if literature is None:
        raise PdfNavigationError("対象Literatureが見つかりません。")
    evidence = get_evidence_reference(connection, checked_evidence_id)
    if evidence is None:
        raise PdfNavigationError("対象Evidenceが見つかりません。")
    if evidence.literature_id != checked_literature_id:
        raise PdfNavigationError(
            "選択されたEvidenceは対象Literatureに属していません。"
        )

    resolved_path, path_error = _resolve_and_validate_pdf_path(
        literature.pdf_path,
        project_root,
    )
    return PdfNavigationTarget(
        literature_id=checked_literature_id,
        literature_title=literature.title,
        stored_pdf_path=literature.pdf_path,
        resolved_pdf_path=resolved_path,
        evidence_id=checked_evidence_id,
        pdf_page=evidence.pdf_page,
        printed_page=evidence.printed_page,
        section=evidence.section,
        subsection=evidence.subsection,
        table_label=evidence.table_label,
        figure_label=evidence.figure_label,
        verification=evidence.verification,
        path_error=path_error,
    )


def format_locator_summary(target: PdfNavigationTarget) -> str:
    """Format every locator independently, including missing values."""
    fields = (
        ("PDF page", target.pdf_page),
        ("Printed page", target.printed_page),
        ("Section", target.section),
        ("Subsection", target.subsection),
        ("Table", target.table_label),
        ("Figure", target.figure_label),
    )
    return "\n".join(
        f"{label}: {'未登録' if value is None else value}"
        for label, value in fields
    )


def _failed_open(
    target: PdfNavigationTarget, message: str = PDF_OPEN_FAILURE_MESSAGE
) -> PdfOpenResult:
    return PdfOpenResult(False, target, message)


def open_evidence_pdf(
    connection: sqlite3.Connection,
    preview_target: PdfNavigationTarget,
    *,
    project_root: object | None = None,
    runner: Optional[Callable[..., object]] = None,
) -> PdfOpenResult:
    """Revalidate a preview and invoke macOS ``open`` without a shell."""
    if not isinstance(preview_target, PdfNavigationTarget):
        raise TypeError("preview_targetはPdfNavigationTargetで指定してください。")

    try:
        current_target = build_navigation_target(
            connection,
            preview_target.literature_id,
            preview_target.evidence_id,
            project_root=project_root,
        )
    except PdfNavigationError as error:
        return _failed_open(preview_target, str(error))

    if not current_target.can_open:
        return _failed_open(
            current_target,
            current_target.path_error or PDF_OPEN_FAILURE_MESSAGE,
        )
    if (
        current_target.stored_pdf_path != preview_target.stored_pdf_path
        or current_target.resolved_pdf_path != preview_target.resolved_pdf_path
    ):
        return _failed_open(
            current_target,
            "Preview後にpdf_pathが変更されました。再度内容を確認してください。",
        )

    assert current_target.resolved_pdf_path is not None
    command_runner = subprocess.run if runner is None else runner
    try:
        completed = command_runner(
            ["open", str(current_target.resolved_pdf_path)],
            shell=False,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return _failed_open(current_target)

    if getattr(completed, "returncode", 1) != 0:
        return _failed_open(current_target)
    return PdfOpenResult(True, current_target)

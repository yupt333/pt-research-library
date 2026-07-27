"""Read-only duplicate-candidate detection for literature records."""

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from src.models import Literature


TITLE_SIMILARITY_THRESHOLD = 0.90

_DOI_PREFIXES = (
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "https://doi.org/",
    "http://doi.org/",
    "doi:",
)
_PMID_PREFIX_PATTERN = re.compile(r"^pmid:", re.IGNORECASE)
_LITERATURE_SELECT_COLUMNS = (
    "id",
    "title",
    "authors",
    "journal",
    "publication_year",
    "volume",
    "issue",
    "pages",
    "doi",
    "pmid",
    "url",
    "language",
    "publication_type",
    "abstract",
    "pdf_path",
    "personal_summary",
    "ai_summary",
    "ai_summary_status",
    "general_note",
    "key_findings",
    "methods_note",
    "clinical_note",
    "limitation_note",
    "relevance_note",
    "evidence_level",
    "verification_status",
    "adoption_status",
    "exclusion_reason",
    "rating",
    "created_at",
    "updated_at",
)


@dataclass(frozen=True)
class DuplicateCandidate:
    """One existing literature record that may duplicate a new record."""

    literature: Literature
    match_reasons: tuple[str, ...]
    title_similarity: float


def normalize_doi(value: object) -> Optional[str]:
    """Normalize a DOI for comparison without checking whether it exists."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("doiはNoneまたは文字列で指定してください。")

    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    if not normalized:
        return None

    for prefix in _DOI_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):].strip()
            break

    return normalized or None


def normalize_pmid(value: object) -> Optional[str]:
    """Normalize and validate a PMID as an ASCII-digit-only string."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("pmidはNoneまたは文字列で指定してください。")

    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        return None

    normalized = _PMID_PREFIX_PATTERN.sub("", normalized, count=1).strip()
    normalized = "".join(normalized.split())
    if not normalized:
        return None
    if not all("0" <= character <= "9" for character in normalized):
        raise ValueError("pmidはASCII数字だけで指定してください。")
    return normalized


def normalize_title(value: object) -> str:
    """Normalize a title while preserving its words and their order."""
    if not isinstance(value, str):
        raise ValueError("titleは空でない文字列で指定してください。")

    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = " ".join(normalized.strip().split())
    normalized = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    )
    normalized = " ".join(normalized.split())
    if not normalized:
        raise ValueError(
            "titleは句読点以外を含む文字列で指定してください。"
        )
    return normalized


def calculate_title_similarity(title_a: object, title_b: object) -> float:
    """Return SequenceMatcher similarity after normalizing both titles."""
    normalized_a = normalize_title(title_a)
    normalized_b = normalize_title(title_b)
    return _calculate_normalized_title_similarity(normalized_a, normalized_b)


def _calculate_normalized_title_similarity(
    normalized_title_a: str,
    normalized_title_b: str,
) -> float:
    """Return SequenceMatcher similarity between normalized titles."""
    return SequenceMatcher(
        None,
        normalized_title_a,
        normalized_title_b,
    ).ratio()


def _normalize_existing_doi(value: object) -> Optional[str]:
    """Normalize one stored DOI, ignoring an unexpected stored type."""
    try:
        return normalize_doi(value)
    except ValueError:
        return None


def _normalize_existing_pmid(value: object) -> Optional[str]:
    """Normalize one stored PMID, ignoring an invalid stored value."""
    try:
        return normalize_pmid(value)
    except ValueError:
        return None


def _list_valid_literature(
    connection: sqlite3.Connection,
) -> list[Literature]:
    """Restore stored literature, skipping rows with a non-string title."""
    columns = ", ".join(_LITERATURE_SELECT_COLUMNS)
    rows = connection.execute(
        f"SELECT {columns} FROM literature ORDER BY id ASC"
    ).fetchall()
    literature_records: list[Literature] = []
    for row in rows:
        if not isinstance(row["title"], str):
            continue
        literature_records.append(Literature(**dict(row)))
    return literature_records


def _calculate_existing_title_similarity(
    normalized_input_title: str,
    existing_title: str,
) -> float:
    """Compare a stored title, using zero when it cannot be normalized."""
    try:
        normalized_existing_title = normalize_title(existing_title)
    except ValueError:
        return 0.0
    return _calculate_normalized_title_similarity(
        normalized_input_title,
        normalized_existing_title,
    )


def find_duplicate_candidates(
    connection: sqlite3.Connection,
    *,
    title: object,
    doi: object = None,
    pmid: object = None,
) -> list[DuplicateCandidate]:
    """Return existing literature that matches an identifier or similar title."""
    normalized_title = normalize_title(title)
    normalized_doi = normalize_doi(doi)
    normalized_pmid = normalize_pmid(pmid)

    candidates: list[DuplicateCandidate] = []
    for literature in _list_valid_literature(connection):
        existing_doi = (
            _normalize_existing_doi(literature.doi)
            if normalized_doi is not None
            else None
        )
        existing_pmid = (
            _normalize_existing_pmid(literature.pmid)
            if normalized_pmid is not None
            else None
        )
        title_similarity = _calculate_existing_title_similarity(
            normalized_title,
            literature.title,
        )

        doi_matches = (
            normalized_doi is not None
            and existing_doi is not None
            and normalized_doi == existing_doi
        )
        pmid_matches = (
            normalized_pmid is not None
            and existing_pmid is not None
            and normalized_pmid == existing_pmid
        )
        title_matches = title_similarity >= TITLE_SIMILARITY_THRESHOLD

        match_reasons = tuple(
            reason
            for reason, matches in (
                ("doi", doi_matches),
                ("pmid", pmid_matches),
                ("title", title_matches),
            )
            if matches
        )
        if match_reasons:
            candidates.append(
                DuplicateCandidate(
                    literature=literature,
                    match_reasons=match_reasons,
                    title_similarity=title_similarity,
                )
            )

    candidates.sort(
        key=lambda candidate: (
            -int("doi" in candidate.match_reasons),
            -int("pmid" in candidate.match_reasons),
            -candidate.title_similarity,
            candidate.literature.id
            if candidate.literature.id is not None
            else 0,
        )
    )
    return candidates

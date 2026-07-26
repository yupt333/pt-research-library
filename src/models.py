"""Data structures used by the literature repository."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Literature:
    """A literature record matching the fields in the literature table."""

    title: str
    id: Optional[int] = None
    authors: Optional[str] = None
    journal: Optional[str] = None
    publication_year: Optional[int] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    url: Optional[str] = None
    language: Optional[str] = None
    publication_type: Optional[str] = None
    abstract: Optional[str] = None
    pdf_path: Optional[str] = None
    personal_summary: Optional[str] = None
    ai_summary: Optional[str] = None
    ai_summary_status: str = "未作成"
    general_note: Optional[str] = None
    key_findings: Optional[str] = None
    methods_note: Optional[str] = None
    clinical_note: Optional[str] = None
    limitation_note: Optional[str] = None
    relevance_note: Optional[str] = None
    evidence_level: Optional[str] = None
    verification_status: str = "未確認"
    adoption_status: str = "未判定"
    exclusion_reason: Optional[str] = None
    rating: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("タイトルは必須です。")
        if self.rating is not None and (
            isinstance(self.rating, bool)
            or not isinstance(self.rating, int)
            or not 1 <= self.rating <= 5
        ):
            raise ValueError(
                "ratingはNoneまたは1〜5の整数で指定してください。"
                f"受け取った値: {self.rating!r}"
            )

"""Data structures used by the literature repository."""

from dataclasses import dataclass
from typing import Any, Optional


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


@dataclass
class Tag:
    """A tag record matching the fields in the tags table."""

    name: str
    id: Optional[int] = None


@dataclass
class UsageHistory:
    """A usage-history record matching the fields in the usage_history table."""

    literature_id: int
    usage_type: Optional[str]
    id: Optional[int] = None
    project_name: Optional[str] = None
    usage_note: Optional[str] = None
    used_at: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class ResearchProject:
    """One user-owned research project, separate from usage history."""

    name: str
    id: Optional[int] = None
    objective: Optional[str] = None
    current_status: Optional[str] = None
    protocol_note: Optional[str] = None
    general_note: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ResearchProjectItem:
    """One project-local concept, question, or next action."""

    project_id: int
    item_type: str
    content: str
    id: Optional[int] = None
    note: Optional[str] = None
    sort_order: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class StructuredEntity:
    """One canonical structured research-data entity."""

    literature_id: int
    entity_type: str
    verification: str
    id: Optional[int] = None
    parent_entity_id: Optional[int] = None
    sort_order: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class StructuredField:
    """One decoded structured field belonging to a structured entity."""

    literature_id: int
    entity_id: int
    field_key: str
    content_role: str
    value: Any
    availability: Optional[str]
    verification: str
    id: Optional[int] = None
    note: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class EvidenceReference:
    """One canonical source locator or exact source quotation."""

    literature_id: int
    verification: str
    id: Optional[int] = None
    pdf_page: Optional[int] = None
    printed_page: Optional[str] = None
    section: Optional[str] = None
    subsection: Optional[str] = None
    table_label: Optional[str] = None
    figure_label: Optional[str] = None
    quote_text: Optional[str] = None
    note: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

"""Data models for drawing search results."""
from dataclasses import dataclass, field


@dataclass
class DrawingResult:
    document_id: str = ""
    drawing_number: str = ""
    title: str = ""
    facility: str = ""
    drawing_type: str = ""
    drawing_subject: str = ""
    paper_size: str = ""
    revision: str = ""
    confidentiality: str = ""
    state: str = ""
    signed_out: bool = False
    physical_location: str = ""
    legacy_doc_number: str = ""

    # URL built from the search host + fetchDocument path
    document_url: str = ""


@dataclass
class PagedResults:
    """A single page of drawing search results with pagination metadata."""
    results: list          # list[DrawingResult]
    page: int = 0
    page_size: int = 50
    total_count: int = 0   # 0 = unknown
    has_next: bool = False

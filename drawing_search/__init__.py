"""drawing_search — corporate drawing search client.

Public API::

    from drawing_search import DrawingSearchClient, SearchParams, DrawingResult

    client = DrawingSearchClient(
        base_url="https://CORPRATEINTERNAL.COM",
        cookies={"filenet-es": "...", "_WL_AUTHCOOKIE_filenet-es": "..."},
    )
    results = client.search(SearchParams(facility="111j", drawing_type="A"))
    for r in results:
        print(r.drawing_number, r.title, r.document_url)
"""

from .models import DrawingResult
from .client import DrawingSearchClient, SearchParams
from .parser import parse_results

__all__ = [
    "DrawingSearchClient",
    "SearchParams",
    "DrawingResult",
    "parse_results",
]

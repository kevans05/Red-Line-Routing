"""Parse JSON responses from the engineering standards tree API."""
from .models import EngineeringSeries, EngineeringStandard, _strip_html


def parse_series(data) -> list:
    """Parse series?group=00all response → list[EngineeringSeries]."""
    result = []
    for item in (data if isinstance(data, list) else []):
        value = (item.get('value') or '').strip()
        title = _strip_html(item.get('title') or '')
        if value:
            result.append(EngineeringSeries(value=value, title=title))
    return result


def parse_section(data, series_value: str, base_url: str) -> list:
    """Recursively parse sections?series=... response → list[EngineeringStandard].

    Folder nodes are recursed into; leaf nodes (folder=False) become standards.
    """
    results = []
    for node in (data if isinstance(data, list) else []):
        if node.get('folder'):
            results.extend(parse_section(
                node.get('children') or [], series_value, base_url))
        else:
            node_value = (node.get('value') or '').strip()
            std_id = _strip_html(node.get('title') or '')
            # URL preference: explicit field → href → construct from node_value
            url = (node.get('url') or node.get('href') or '').strip()
            if not url and node_value and base_url:
                url = f"{base_url.rstrip('/')}/fetchDocument.html?documentId={node_value}"
            results.append(EngineeringStandard(
                standard_id=std_id,
                description=(node.get('description') or '').strip(),
                series_value=series_value,
                node_value=node_value,
                release=(node.get('release') or '').strip(),
                major_version=int(node.get('majorVersion') or 0),
                document_state=(node.get('documentState') or '').strip(),
                document_date=int(node.get('documentDate') or 0),
                version_series_id=(node.get('versionSeriesId') or '').strip(),
                revision_serial_id=(node.get('revisionSerialId') or '').strip(),
                url=url,
            ))
    return results

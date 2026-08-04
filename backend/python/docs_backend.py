from pathlib import Path
import re

from fastapi import APIRouter, Request

try:
    from .services.auth_service import is_guest_user, require_request_auth
except ImportError:
    from services.auth_service import is_guest_user, require_request_auth


router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DOC_CATEGORY_META = {
    'Start Here': {
        'classification': 'Current Reference',
        'tone': 'current',
        'description': 'Entry-point material for understanding the public Robotineeko snapshot.',
    },
    'Guides And Operations': {
        'classification': 'Operational Guide',
        'tone': 'guide',
        'description': 'Workflow-oriented notes that explain how the product is intended to be used.',
    },
}

GUEST_VISIBLE_DOC_IDS = {
    'robotineeko-overview',
    'operator-quickstart',
    'public-surfaces-and-access-modes',
    'broker-and-cost-models',
    'research-to-trader-workflow',
}

DOC_ENTRIES = [
    {
        'id': 'robotineeko-overview',
        'title': 'Robotineeko Overview',
        'category': 'Start Here',
        'path': PROJECT_ROOT / 'docs' / 'robotineeko-overview.md',
    },
    {
        'id': 'operator-quickstart',
        'title': 'Operator Quickstart',
        'category': 'Start Here',
        'path': PROJECT_ROOT / 'docs' / 'operator-quickstart.md',
    },
    {
        'id': 'public-surfaces-and-access-modes',
        'title': 'Public Surfaces And Access Modes',
        'category': 'Start Here',
        'path': PROJECT_ROOT / 'docs' / 'public-surfaces-and-access-modes.md',
    },
    {
        'id': 'broker-and-cost-models',
        'title': 'Broker And Cost Models',
        'category': 'Guides And Operations',
        'path': PROJECT_ROOT / 'docs' / 'broker-and-cost-models.md',
    },
    {
        'id': 'research-to-trader-workflow',
        'title': 'Research To Trader Workflow',
        'category': 'Guides And Operations',
        'path': PROJECT_ROOT / 'docs' / 'research-to-trader-workflow.md',
    },
]


def _slugify_heading(text: str):
    safe = re.sub(r'[^a-zA-Z0-9]+', '-', str(text or '').strip().lower()).strip('-')
    return safe or 'section'


def _parse_document_sections(content: str):
    lines = str(content or '').splitlines()
    sections = []
    current = {
        'id': 'overview',
        'title': 'Overview',
        'level': 1,
        'content_lines': [],
    }

    for raw_line in lines:
        heading_match = re.match(r'^(#{1,6})\s+(.*)$', raw_line)
        if heading_match:
            if current['content_lines'] or current['title'] != 'Overview':
                sections.append({
                    'id': current['id'],
                    'title': current['title'],
                    'level': current['level'],
                    'content': '\n'.join(current['content_lines']).strip(),
                })
            title = heading_match.group(2).strip()
            current = {
                'id': _slugify_heading(title),
                'title': title,
                'level': len(heading_match.group(1)),
                'content_lines': [],
            }
            continue

        current['content_lines'].append(raw_line)

    if current['content_lines'] or current['title'] != 'Overview':
        sections.append({
            'id': current['id'],
            'title': current['title'],
            'level': current['level'],
            'content': '\n'.join(current['content_lines']).strip(),
        })

    return sections


def _derive_document_summary(content: str):
    lines = str(content or '').splitlines()
    in_code = False
    paragraph_lines = []

    for raw_line in lines:
        line = raw_line.strip()

        if line.startswith('```'):
            in_code = not in_code
            continue

        if in_code:
            continue

        if not line:
            if paragraph_lines:
                break
            continue

        if line.startswith('#'):
            if paragraph_lines:
                break
            continue

        if re.match(r'^[-*]\s+', line) or re.match(r'^\d+\.\s+', line):
            if paragraph_lines:
                break
            continue

        paragraph_lines.append(line)

    summary = ' '.join(paragraph_lines).strip()
    if len(summary) > 220:
        summary = summary[:217].rsplit(' ', 1)[0].strip() + '...'

    return summary


def _visible_doc_entries_for_auth_user(auth_user: dict | None):
    if not is_guest_user(auth_user):
        return list(DOC_ENTRIES)
    return [entry for entry in DOC_ENTRIES if str(entry.get('id') or '').strip() in GUEST_VISIBLE_DOC_IDS]


def _load_document(entry: dict, *, include_path: bool = True):
    path = entry['path']
    try:
        content = path.read_text(encoding='utf-8')
    except Exception as error:
        content = f'Failed to read document: {error}'

    sections = _parse_document_sections(content)
    category_meta = DOC_CATEGORY_META.get(entry['category'], {})
    return {
        'id': entry['id'],
        'title': entry['title'],
        'category': entry['category'],
        'classification': str(category_meta.get('classification') or 'Reference'),
        'classification_tone': str(category_meta.get('tone') or 'neutral'),
        'category_description': str(category_meta.get('description') or ''),
        'summary': str(entry.get('summary') or _derive_document_summary(content)),
        'path': str(path.relative_to(PROJECT_ROOT)) if include_path else '',
        'content': content,
        'sections': sections,
    }


@router.get('/system/docs')
def get_project_docs(request: Request):
    auth_user = require_request_auth(request)
    is_guest = is_guest_user(auth_user)
    documents = [
        _load_document(entry, include_path=not is_guest)
        for entry in _visible_doc_entries_for_auth_user(auth_user)
    ]
    return {
        'status': 'ok',
        'documents': documents,
        'guest_curated': is_guest,
    }

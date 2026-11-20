"""Verify immutable acquisition resources before a parser reads them."""
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import urlsplit

from .ingest import file_hash
from .parsers import SourceError


def https_url(value):
    parsed = urlsplit(value or '')
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise SourceError('An HTTPS source URL without credentials is required')
    return value


def verified_resource(manifest, dataset, select):
    manifest = Path(manifest).resolve(strict=True)
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('status') != 'complete':
        raise SourceError('Acquisition is incomplete')
    files = [f for f in data['files'] if f.get('dataset') == dataset and f.get('status') == 'complete' and select(f)]
    if len(files) != 1:
        raise SourceError('Expected one matching resource for ' + dataset)
    f = files[0]
    path = Path(f['path']).resolve(strict=True)
    if not path.is_relative_to(manifest.parent):
        raise SourceError('Resource escaped acquisition directory')
    if f.get('licence') not in ('Creative Commons Attribution 4.0 International', 'CC BY 4.0'):
        raise SourceError('Resource licence requires review')
    if path.stat().st_size != f['bytes'] or file_hash(path) != f['sha256']:
        raise SourceError('Resource checksum or size mismatch')
    retrieved = datetime.fromisoformat(f['retrieved_at'])
    if retrieved.tzinfo is None:
        raise SourceError('Retrieval timestamp needs timezone')
    source = f.get('source_url') or next(d['source_url'] for d in data['datasets'] if d['key'] == dataset)
    return path, dict(sha256=f['sha256'], source=dataset, source_url=https_url(source),
                      download_url=https_url(f.get('download_url') or f.get('url') or source),
                      licence=f['licence'], licence_url=https_url(f['licence_url']), retrieved_at=retrieved)

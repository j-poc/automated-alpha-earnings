"""Shared cloud acquisition for SEC cache misses; local operation stays explicit."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def enabled():
    return os.environ.get('DCC_SEC_REQUIRED') == '1' or bool(os.environ.get('DCC_SEC_URL') or os.environ.get('DCC_SEC_TOKEN') or os.environ.get('DCC_SEC_CONNECTION'))


def fetch_text(url, *, ttl, cache):
    from dcc_sec import connection, fetch_bytes
    app = 'earnings-cloud'
    config = connection(app=app)
    if urlsplit(config.url).scheme != 'https':
        raise RuntimeError('The public earnings build requires a trusted HTTPS SEC owner')
    response = fetch_bytes(url, app=app, max_bytes=64 * 1024**2,
                           ttl_seconds=min(86400, ttl if ttl is not None else 86400),
                           timeout_seconds=25, refresh=False)
    receipts = Path(cache) / 'sec-receipts'
    receipts.mkdir(parents=True, exist_ok=True)
    record = {key: getattr(response, key) for key in ('url', 'receipt_id', 'sha256', 'retrieved_at', 'completed_at', 'served_at', 'delivery')}
    record['cache_representation'] = 'UTF-8 decoded text; sha256 identifies original owner bytes'
    destination = receipts / (hashlib.sha256(url.encode()).hexdigest() + '.json')
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, indent=2) + '\n')
    temporary.replace(destination)
    return response.body.decode('utf-8', 'replace')

"""Read one bounded SEC resource through the real build caller in a fresh app cache."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import season
import shared_sec

if not shared_sec.enabled():
    raise RuntimeError('Shared cloud SEC configuration is required')
with tempfile.TemporaryDirectory(prefix='earnings-sec-check-') as temporary:
    season.CACHE = Path(temporary)
    body = season.fetch('https://data.sec.gov/submissions/CIK0000320193.json', ttl=1800)
    assert json.loads(body)['cik'] == '0000320193'
    records = list((season.CACHE / 'sec-receipts').glob('*.json'))
    assert len(records) == 1
    print(json.dumps({'proof': 'earnings-cloud-shared-sec', **json.loads(records[0].read_text())}))

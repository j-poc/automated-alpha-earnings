import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import season
import shared_sec


class SharedSecTests(unittest.TestCase):
    def test_cloud_cache_miss_uses_shared_source_and_records_raw_identity(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(season, 'CACHE', Path(tmp)), patch.dict(os.environ, {'DCC_SEC_REQUIRED':'1'}), patch('dcc_sec.connection', return_value=SimpleNamespace(url='https://sec.example.org')):
            result=SimpleNamespace(body=b'{"cik":320193}', url='https://data.sec.gov/submissions/CIK0000320193.json',receipt_id='receipt',sha256=hashlib.sha256(b'{"cik":320193}').hexdigest(),retrieved_at='original',completed_at='checked',served_at='now',delivery='cache')
            with patch('dcc_sec.fetch_bytes', return_value=result) as owner, patch.object(season.urllib.request, 'urlopen') as direct:
                self.assertEqual(season.fetch(result.url, ttl=1800), result.body.decode())
                owner.assert_called_once(); direct.assert_not_called()
                self.assertEqual(season.fetch(result.url, ttl=1800), result.body.decode())
                owner.assert_called_once()
            record=json.loads(next((Path(tmp)/'sec-receipts').glob('*.json')).read_text())
            self.assertEqual(record['sha256'], result.sha256)
            self.assertEqual(record['retrieved_at'], 'original')

    def test_shared_failure_never_falls_back_to_publisher(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(season,'CACHE',Path(tmp)), patch.dict(os.environ,{'DCC_SEC_REQUIRED':'1'}), patch('dcc_sec.connection',side_effect=RuntimeError('offline')), patch.object(season.urllib.request,'urlopen') as direct:
            with self.assertRaisesRegex(RuntimeError,'offline'):
                season.fetch('https://data.sec.gov/submissions/CIK0000320193.json',ttl=1800)
            direct.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_cloud_rejects_loopback(self):
        with patch('dcc_sec.connection',return_value=SimpleNamespace(url='http://127.0.0.1:8931')):
            with self.assertRaisesRegex(RuntimeError,'HTTPS'):
                shared_sec.fetch_text('https://data.sec.gov/submissions/CIK0000320193.json',ttl=1800,cache='unused')

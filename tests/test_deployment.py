"""A refresh succeeds only when production serves the exact built dashboard."""
import base64
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.deploy_vercel import deploy


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.html = Path(self.directory.name) / 'index.html'
        self.html.write_bytes(b'<html>verified snapshot</html>')

    def run_deploy(self):
        return deploy(self.html, 'test-credential', 'evoa3', 'evoa-team', 'earnings')

    @patch('scripts.deploy_vercel.time.sleep')
    @patch('scripts.deploy_vercel.read_live')
    @patch('scripts.deploy_vercel.api')
    @patch('scripts.deploy_vercel.check', return_value={'name': 'automated-alpha-earnings'})
    def test_waits_for_readiness_alias_and_exact_public_page(self, access, api, live, sleep):
        api.side_effect = [
            {'id': 'dpl-test'}, {'readyState': 'BUILDING'},
            {'readyState': 'READY', 'aliasAssigned': False},
            {'readyState': 'READY', 'aliasAssigned': True},
        ]
        live.side_effect = [b'previous snapshot', self.html.read_bytes()]
        with redirect_stdout(StringIO()):
            self.assertEqual(self.run_deploy(), 'dpl-test')
        payload = api.call_args_list[0].args[2]
        self.assertEqual(payload['project'], 'earnings')
        self.assertEqual(payload['target'], 'production')
        self.assertEqual(base64.b64decode(payload['files'][0]['data']), self.html.read_bytes())
        self.assertEqual(live.call_count, 2)

    @patch('scripts.deploy_vercel.read_live')
    @patch('scripts.deploy_vercel.api')
    @patch('scripts.deploy_vercel.check', return_value={'name': 'automated-alpha-earnings'})
    def test_failed_deployment_cannot_report_success(self, access, api, live):
        api.side_effect = [{'id': 'dpl-test'}, {'readyState': 'ERROR'}]
        with redirect_stdout(StringIO()), self.assertRaisesRegex(RuntimeError, 'failed'):
            self.run_deploy()
        live.assert_not_called()

    @patch('scripts.deploy_vercel.time.sleep')
    @patch('scripts.deploy_vercel.read_live', return_value=b'previous snapshot')
    @patch('scripts.deploy_vercel.api')
    @patch('scripts.deploy_vercel.check', return_value={'name': 'automated-alpha-earnings'})
    def test_stale_production_page_cannot_report_success(self, access, api, live, sleep):
        api.side_effect = [{'id': 'dpl-test'}, {'readyState': 'READY', 'aliasAssigned': True}]
        with redirect_stdout(StringIO()), self.assertRaisesRegex(RuntimeError, 'does not serve'):
            self.run_deploy()

    @patch('scripts.deploy_vercel.api')
    @patch('scripts.deploy_vercel.check', side_effect=RuntimeError('wrong workspace'))
    def test_wrong_workspace_never_starts_deployment(self, access, api):
        with self.assertRaisesRegex(RuntimeError, 'wrong workspace'):
            self.run_deploy()
        api.assert_not_called()


if __name__ == '__main__':
    unittest.main()

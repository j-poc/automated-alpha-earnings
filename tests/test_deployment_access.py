"""An otherwise valid token for another account must never start deployment."""
import unittest
from contextlib import redirect_stdout
from io import StringIO
from scripts.check_vercel_access import check


class DeploymentAccessTests(unittest.TestCase):
    def test_correct_account_and_project_pass(self):
        def request(path, token):
            return {'user': {'username': 'evoa3'}} if path == '/v2/user' else {
                'id': 'earnings', 'accountId': 'evoa-team', 'name': 'automated-alpha-earnings'}
        with redirect_stdout(StringIO()):
            check('test-credential', 'evoa3', 'evoa-team', 'earnings', request)

    def test_other_account_or_project_is_rejected(self):
        def wrong_account(path, token): return {'user': {'username': 'another-account'}}
        with self.assertRaisesRegex(RuntimeError, 'different account'):
            check('test-credential', 'evoa3', 'evoa-team', 'earnings', wrong_account)
        def wrong_project(path, token):
            return {'user': {'username': 'evoa3'}} if path == '/v2/user' else {'id': 'other', 'accountId': 'evoa-team'}
        with self.assertRaisesRegex(RuntimeError, 'configured Evoa earnings project'):
            check('test-credential', 'evoa3', 'evoa-team', 'earnings', wrong_project)

    def test_missing_secret_is_rejected_without_a_network_request(self):
        def request(path, token): raise AssertionError('unexpected network call')
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            check('', 'evoa3', 'evoa-team', 'earnings', request)

if __name__ == '__main__': unittest.main()

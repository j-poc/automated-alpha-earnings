"""An otherwise valid token for another account must never start deployment."""
import unittest
from contextlib import redirect_stdout
from io import StringIO
from scripts.check_vercel_access import AccessError, check


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

    def test_project_only_token_can_verify_exact_workspace_and_project(self):
        for status in (403, 404):
            def request(path, token):
                if path == '/v2/user':
                    raise AccessError(path, status)
                return {'id': 'earnings', 'accountId': 'evoa-team', 'name': 'automated-alpha-earnings'}
            with self.subTest(status=status), redirect_stdout(StringIO()):
                check('test-credential', 'evoa3', 'evoa-team', 'earnings', request)
            def wrong_workspace(path, token):
                if path == '/v2/user':
                    raise AccessError(path, status)
                return {'id': 'earnings', 'accountId': 'other-team', 'name': 'automated-alpha-earnings'}
            with self.subTest(status=status), self.assertRaisesRegex(RuntimeError, 'configured Evoa earnings project'):
                check('test-credential', 'evoa3', 'evoa-team', 'earnings', wrong_workspace)

    def test_invalid_token_cannot_skip_identity_check(self):
        def request(path, token):
            raise AccessError(path, 401)
        with self.assertRaisesRegex(AccessError, 'HTTP 401'):
            check('test-credential', 'evoa3', 'evoa-team', 'earnings', request)

if __name__ == '__main__': unittest.main()

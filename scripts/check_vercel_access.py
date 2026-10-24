"""Fail before a refresh if its deployment token targets the wrong account/project.

Only the account handle and project name are logged. Credentials and API response
bodies never appear in errors or output.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


class AccessError(RuntimeError):
    def __init__(self, path, status):
        self.status = status
        endpoint = path.split('?', 1)[0]
        super().__init__(f'Vercel denied {endpoint} (HTTP {status}). '
                         'Check that VERCEL_TOKEN can access the configured Evoa earnings project.')


def get(path, token):
    request = urllib.request.Request('https://api.vercel.com' + path,
                                     headers={'Authorization': 'Bearer ' + token})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise AccessError(path, exc.code) from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise RuntimeError('Could not verify Vercel access; no refresh or deployment was started.') from None


def check(token, expected_user, team, project, request=get):
    if not token:
        raise RuntimeError('VERCEL_TOKEN is missing. Add the account token to the repository Actions secret.')
    try:
        user = request('/v2/user', token).get('user') or {}
    except AccessError as exc:
        if exc.status not in (403, 404):
            raise
        user = None  # Project-only tokens do not expose the account profile.
    if user is not None and user.get('username') != expected_user:
        raise RuntimeError('VERCEL_TOKEN belongs to a different account. Use the expected Evoa3 account.')
    query = urllib.parse.urlencode({'teamId': team})
    target = request('/v9/projects/' + urllib.parse.quote(project, safe='') + '?' + query, token)
    if target.get('id') != project or target.get('accountId') != team:
        raise RuntimeError('VERCEL_TOKEN cannot verify the configured Evoa earnings project.')
    identity = expected_user if user is not None else 'project-scoped token'
    print(f"Vercel access verified: {identity} → {target['name']} in the configured Evoa workspace")


def main():
    try:
        check(os.environ.get('VERCEL_TOKEN'), os.environ['VERCEL_EXPECTED_USER'],
              os.environ['VERCEL_ORG_ID'], os.environ['VERCEL_PROJECT_ID'])
    except (RuntimeError, KeyError) as exc:
        print('Deployment access check failed: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

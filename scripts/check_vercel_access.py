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


def get(path, token):
    request = urllib.request.Request('https://api.vercel.com' + path,
                                     headers={'Authorization': 'Bearer ' + token})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        endpoint = path.split('?', 1)[0]
        raise RuntimeError(f'Vercel denied {endpoint} (HTTP {exc.code}). '
                           'Check that VERCEL_TOKEN can access the configured Evoa earnings project.') from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise RuntimeError('Could not verify Vercel access; no refresh or deployment was started.') from None


def check(token, expected_user, team, project, request=get):
    if not token:
        raise RuntimeError('VERCEL_TOKEN is missing. Add the account token to the repository Actions secret.')
    user = request('/v2/user', token).get('user') or {}
    if user.get('username') != expected_user:
        raise RuntimeError('VERCEL_TOKEN belongs to a different account. Use the expected Evoa3 account.')
    query = urllib.parse.urlencode({'teamId': team})
    target = request('/v9/projects/' + urllib.parse.quote(project, safe='') + '?' + query, token)
    if target.get('id') != project or target.get('accountId') != team:
        raise RuntimeError('VERCEL_TOKEN cannot verify the configured Evoa earnings project.')
    print(f"Vercel access verified: {expected_user} → {target['name']} in the configured Evoa workspace")


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

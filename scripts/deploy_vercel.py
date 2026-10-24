"""Deploy the single-file dashboard without account-wide CLI permissions."""
import base64
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from scripts.check_vercel_access import check


LIVE_URL = 'https://automatedalpha-earnings.vercel.app/'


def api(path, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request('https://api.vercel.com' + path, data=data,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Vercel deployment request failed (HTTP {exc.code}, {path.split("?", 1)[0]}).') from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise RuntimeError('Vercel deployment request failed; check the deployment state before retrying.') from None


def read_live(digest):
    request = urllib.request.Request(LIVE_URL + '?snapshot=' + digest,
                                     headers={'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def deploy(html, token, expected_user, team, project):
    target = check(token, expected_user, team, project)
    content = html.read_bytes()
    if not content:
        raise RuntimeError('The generated dashboard is empty; deployment was not started.')
    query = '?' + urllib.parse.urlencode({'teamId': team})
    deployment = api('/v13/deployments' + query, token, {
        'name': target['name'], 'project': project, 'target': 'production',
        'files': [{'file': 'index.html', 'encoding': 'base64',
                   'data': base64.b64encode(content).decode()}],
        'projectSettings': {'framework': None, 'buildCommand': None,
                            'installCommand': None, 'outputDirectory': None, 'rootDirectory': None},
    })
    deployment_id = deployment['id']
    print(f'Created production deployment {deployment_id}', flush=True)
    previous = None
    for _ in range(120):
        state = api('/v13/deployments/' + urllib.parse.quote(deployment_id, safe='') + query, token)
        ready = state['readyState']
        if ready != previous:
            print('Deployment state: ' + ready, flush=True)
            previous = ready
        if ready in ('ERROR', 'CANCELED') or state.get('aliasError'):
            raise RuntimeError(f'Production deployment {deployment_id} failed; inspect it in Vercel.')
        if ready == 'READY' and state.get('aliasAssigned'):
            break
        time.sleep(5)
    else:
        raise RuntimeError(f'Timed out waiting for production deployment {deployment_id}.')
    digest = hashlib.sha256(content).hexdigest()
    for _ in range(12):
        try:
            if read_live(digest) == content:
                print(f'Production verified: {LIVE_URL} SHA-256 {digest}', flush=True)
                return deployment_id
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(5)
    raise RuntimeError('The production URL does not serve the deployed snapshot; data was not committed.')

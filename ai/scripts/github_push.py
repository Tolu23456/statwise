#!/usr/bin/env python3
"""Push code + model to GitHub and create release v1.0.1"""
import os, subprocess, sys, json, urllib.request, urllib.error

PAT  = os.environ['GH_PAT']
REPO = 'Tolu23456/statwise'
CWD  = '/home/runner/workspace'
LFS  = '/nix/store/x62fkcgn4andbmsa8w045qfqk59g0h6k-git-lfs-3.6.1/bin/git-lfs'
REMOTE = f'https://{PAT}@github.com/{REPO}.git'

env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'HOME': '/home/runner',
       'PATH': '/nix/store/hm5p1jkyrqp2jinklggxv8q7qg1glf03-replit-runtime-path/bin:/usr/bin:/bin'}

def run(cmd, **kw):
    r = subprocess.run(cmd, cwd=CWD, capture_output=True, text=True, env=env, **kw)
    safe_out = (r.stdout + r.stderr).replace(PAT, '***')
    print(f'$ {" ".join(str(c) for c in cmd)}\n  -> exit {r.returncode}: {safe_out[:300]}')
    return r

def api(method, path, data=None, headers=None):
    url = f'https://api.github.com{path}'
    h = {'Authorization': f'token {PAT}', 'Accept': 'application/vnd.github+json',
         'X-GitHub-Api-Version': '2022-11-28'}
    if headers: h.update(headers)
    body = json.dumps(data).encode() if data else None
    req  = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

# ── 1. git config ────────────────────────────────────────────────
run(['git', 'config', 'user.email', 'bot@statwise.ai'])
run(['git', 'config', 'user.name',  'StatWise Bot'])

# ── 2. LFS init (local only) ─────────────────────────────────────
run([LFS, 'install', '--local', '--force'])
run(['git', 'config', 'lfs.url', f'https://{PAT}@github.com/{REPO}.git/info/lfs'])

# ── 3. Push LFS objects ──────────────────────────────────────────
print('\n=== Pushing LFS objects (557 MB – please wait) ===')
r = run([LFS, 'push', '--all', REMOTE])
if r.returncode != 0:
    print('LFS push FAILED – aborting')
    sys.exit(1)
print('LFS push OK')

# ── 4. Push code ─────────────────────────────────────────────────
print('\n=== Pushing commits to GitHub ===')
r = run(['git', 'push', REMOTE, 'main'])
if r.returncode != 0:
    print('Git push FAILED – aborting')
    sys.exit(1)
print('Git push OK')

# ── 5. Create release v1.0.1 ─────────────────────────────────────
print('\n=== Creating release v1.0.1 ===')
status, body = api('POST', f'/repos/{REPO}/releases', {
    'tag_name':         'v1.0.1',
    'target_commitish': 'main',
    'name':             'StatWise v1.0.1 – 5-model stacking ensemble',
    'body': (
        '## Changes\n'
        '- Added MLP neural network to 5-model stacking ensemble\n'
        '- Fixed draw prediction (0% → 20% recall)\n'
        '- Batch inference: 143 matches in <5 s (was >5 min)\n\n'
        '## Model asset\n'
        '`football_predictor.pkl` – XGBoost × 3 + Random Forest + MLP Neural Network stacked ensemble (557 MB)\n'
    ),
    'draft':      False,
    'prerelease': False,
})
print(f'Create release: {status}')
if status not in (200, 201):
    print('Error:', body)
    sys.exit(1)

release_id      = body['id']
upload_url_base = body['upload_url'].split('{')[0]
print(f'Release ID: {release_id}  upload_url: {upload_url_base}')

# ── 6. Upload .pkl as release asset ─────────────────────────────
PKL = '/home/runner/workspace/ai/models/football_predictor.pkl'
print(f'\n=== Uploading {PKL} as release asset ===')
size = os.path.getsize(PKL)
print(f'File size: {size/1024/1024:.1f} MB')

upload_url = f'{upload_url_base}?name=football_predictor.pkl&label=Trained+model+%28v1.0.1%29'
h = {
    'Authorization': f'token {PAT}',
    'Accept':        'application/vnd.github+json',
    'Content-Type':  'application/octet-stream',
    'Content-Length': str(size),
}
with open(PKL, 'rb') as f:
    req = urllib.request.Request(upload_url, data=f, headers=h, method='POST')
    try:
        with urllib.request.urlopen(req) as resp:
            asset = json.loads(resp.read())
            print(f'Asset upload OK: {asset.get("browser_download_url", "?")}')
    except urllib.error.HTTPError as e:
        err = e.read()
        print(f'Asset upload FAILED {e.code}: {err[:500]}')
        sys.exit(1)

print('\n✓ All done!')

"""Dependency-free, safe local check of the NastaraOps presentation layer."""
from pathlib import Path
import hashlib
import re

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    'overview': 'Overview', 'workflows': 'Workflows', 'knowledge': 'Knowledge Base',
    'evaluations': 'Evaluations', 'benchmarks': 'Benchmarks', 'reliability': 'Reliability Lab',
    'human_review': 'Blinded Review', 'single_review': 'My Reviews',
    'research_v9': 'Research Reports', 'approvals': 'Approvals',
    'memory': 'Memory', 'audit': 'Audit Trail',
}
FROZEN_JUDGE = '9cdb1a2d655c8d3cf87eec86cc7a794129219b8fa306fcbae1dfd57de40262f2'
FROZEN_HOLDOUT = 'aed93ff49aeddf533f357f55a8a5fd36d8bcaff9879a88489e8de9d1f908af25'


def check() -> list[str]:
    errors=[]
    html=(ROOT/'app/static/index.html').read_text(encoding='utf-8')
    js=(ROOT/'app/static/app.js').read_text(encoding='utf-8')
    api=(ROOT/'app/main.py').read_text(encoding='utf-8')
    if '<title>NastaraOps' not in html or 'Nastaran Zarafshan' not in html:
        errors.append('Brand or footer signature missing')
    if html.count('class="nav-group"') != 3:
        errors.append('Expected three navigation groups')
    nav=re.findall(r'<button[^>]+class="nav-link[^>]+data-tab="([^"]+)"[^>]*>(.*?)</button>',html,re.S)
    if len(nav)!=len(LABELS) or set(k for k,_ in nav)!=set(LABELS):
        errors.append('Navigation page IDs or count changed')
    for key,body in nav:
        expected=LABELS.get(key)
        if expected and f'class="nav-text">{expected}</span>' not in body:
            errors.append(f'Wrong navigation label for {key}')
        if re.search(r'\bV\d+(?:\.\d+)?\b',body):
            errors.append(f'Version marker in navigation item {key}')
        if f'id="{key}" class="tab-page' not in html:
            errors.append(f'Missing page target {key}')
    if 'const display=link?.querySelector' not in js:
        errors.append('Breadcrumb labels not connected to navigation')
    if '"nastaraops-icon.svg"' not in api:
        errors.append('Icon route missing from the static asset allowlist')
    for name,expect in [('app/benchmark.py',FROZEN_JUDGE),('examples/benchmark/holdout_cases.json',FROZEN_HOLDOUT)]:
        digest=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        if digest!=expect: errors.append(f'Frozen research material changed: {name}')
    if (ROOT/'docker-compose.yml').read_text(encoding='utf-8').splitlines()[0].strip() != 'name: nastaraops-native':
        errors.append('Native deployment must use isolated Compose identity to protect old volumes')
    return errors


if __name__ == '__main__':
    issues=check()
    if issues:
        for item in issues: print('FAIL:',item)
        raise SystemExit(1)
    print('PASS: NastaraOps branding, navigation, footer, isolated Compose identity and frozen research checks')

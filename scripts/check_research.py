"""Checks source publication safety and frozen study provenance. No secrets printed."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN_JUDGE = '9cdb1a2d655c8d3cf87eec86cc7a794129219b8fa306fcbae1dfd57de40262f2'
FROZEN_DATASET = 'aed93ff49aeddf533f357f55a8a5fd36d8bcaff9879a88489e8de9d1f908af25'
REQUIRED = [
    'app/research_report.py', 'scripts/generate_research_report.py', 'reports/public/RESEARCH_REPORT.md',
    'reports/public/research_metrics.json', 'README.md', 'docker-compose.yml',
    'scripts/setup.ps1', 'scripts/start.ps1', 'scripts/stop.ps1',
]
FORBIDDEN_NAME = ('agentops-v8_1-single-review.json', 'agentops-v7-e9c4a51c.json')


def check_repository(root: Path = ROOT) -> list[str]:
    problems = []
    for item in REQUIRED:
        if not (root / item).is_file():
            problems.append(f'Missing required file: {item}')
    for file_name, expected in [('app/benchmark.py', FROZEN_JUDGE),
                                ('examples/benchmark/holdout_cases.json', FROZEN_DATASET)]:
        path = root / file_name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            problems.append(f'Frozen evidence drifted: {file_name}')
    public_dir = root / 'reports' / 'public'
    if public_dir.exists():
        for path in public_dir.rglob('*'):
            if path.is_file() and path.suffix.lower() in {'.xlsx', '.xls', '.csv'}:
                problems.append(f'Unexpected public raw spreadsheet/CSV: {path.relative_to(root)}')
            if path.is_file() and path.name in FORBIDDEN_NAME:
                problems.append(f'Original user export in public output: {path.relative_to(root)}')
    metrics_path = public_dir / 'research_metrics.json'
    if metrics_path.exists():
        try:
            m = json.loads(metrics_path.read_text(encoding='utf-8'))
            if m.get('human_export_embedded') is not False:
                problems.append('Public metrics do not assert source export is omitted')
            if m.get('dataset_sha256') != FROZEN_DATASET or m.get('judge_source_sha256') != FROZEN_JUDGE:
                problems.append('Public report provenance SHA-256 mismatch')
            if m.get('human_review', {}).get('first_votes') != 100:
                problems.append('Public report incomplete human snapshot')
        except (ValueError, OSError):
            problems.append('Public metrics JSON is not readable')
    # `git ls-files` is authoritative for publication; a local ignored .env is fine.
    try:
        proc = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'],
                              capture_output=True, check=False, timeout=8)
        if proc.returncode == 0 and proc.stdout:
            tracked = [p.decode('utf-8', errors='replace') for p in proc.stdout.split(b'\0') if p]
            for rel in tracked:
                basename = Path(rel).name
                if (basename == '.env' or basename.startswith('.env.') and basename != '.env.example'
                        or basename in FORBIDDEN_NAME or rel.startswith('reports/private/')
                        or rel.startswith('.local/')):
                    problems.append(f'Private file is tracked in Git: {rel}')
    except (OSError, subprocess.TimeoutExpired):
        pass
    return problems


if __name__ == '__main__':
    failures = check_repository()
    if failures:
        for failure in failures:
            print('FAIL:', failure)
        raise SystemExit(1)
    print('PASS: NastaraOps package files, frozen research hashes, provenance, and public-output exclusions')
    print('REMINDER: Review `git status --short` and `git diff --cached` before pushing. Static checks cannot prove production security.')

"""V7 holdout and repeated-run reliability statistics; no gold answers go to the judge.

The judge implementation in app/benchmark.py is frozen at 2.2.0-anchor-review.
Confidence intervals resample source-ENTITY clusters, preserving repeat clustering.
They are descriptive intervals over 25 source entities in an author-curated suite;
not population-level or production guarantees.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.benchmark import (CALIBRATED_RUNNER_VERSION, DATASET, LABELS,
                           summarise_predictions, validate_gold_set)

ROOT = Path(__file__).resolve().parents[1]
HOLDOUT_FILE = ROOT / 'examples' / 'benchmark' / 'holdout_cases.json'
HOLDOUT_ID = 'agentops-holdout-v7-100'
BOOTSTRAP_SEED = 20261104
BOOTSTRAP_ITERATIONS = 1200


def load_dataset(dataset_id: str) -> tuple[dict, str]:
    if dataset_id == HOLDOUT_ID:
        path = HOLDOUT_FILE
    elif dataset_id == 'agentops-synthetic-gold-v1':
        path = DATASET
    else:
        raise ValueError('Unknown benchmark dataset ID; resume is disabled to protect version pinning')
    raw = path.read_bytes()
    data = json.loads(raw)
    if data['dataset_id'] != dataset_id:
        raise ValueError('Dataset ID does not match file metadata')
    validate_gold_set(data)
    if dataset_id == HOLDOUT_ID:
        validate_holdout(data)
    return data, hashlib.sha256(raw).hexdigest()


def validate_holdout(dataset: dict) -> None:
    cases = dataset['cases']
    if len(cases) != 100:
        raise ValueError('V7 holdout must contain exactly 100 cases')
    count = Counter(x['gold_verdict'] for x in cases)
    if any(count[label] != 25 for label in LABELS):
        raise ValueError('V7 holdout must contain 25 cases for every gold class')
    if len({x['candidate'].casefold().strip() for x in cases}) != len(cases):
        raise ValueError('Duplicate candidate statements in V7 holdout')
    original = json.loads(DATASET.read_text(encoding='utf-8'))
    orig_candidates = {x['candidate'].casefold().strip() for x in original['cases']}
    if any(x['candidate'].casefold().strip() in orig_candidates for x in cases):
        raise ValueError('Holdout repeats a V5 development candidate')


def freeze_hash() -> str:
    """Hash of V6.2 judge implementation; stored to detect drift in future runs."""
    return hashlib.sha256((ROOT / 'app' / 'benchmark.py').read_bytes()).hexdigest()


def holdout_manifest() -> dict:
    data, checksum = load_dataset(HOLDOUT_ID)
    return {'dataset_id': data['dataset_id'], 'dataset_sha256': checksum,
            'cases': len(data['cases']), 'labels': dict(Counter(x['gold_verdict'] for x in data['cases'])),
            'runner_version': CALIBRATED_RUNNER_VERSION, 'judge_source_sha256': freeze_hash(),
            'provenance': data['provenance'], 'limitations': data['limitations'],
            'evaluation_protocol': data['evaluation_protocol'],
            'recommended_repeats': 3, 'supported_repeats': [2, 3, 5]}


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _std(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    mu = sum(xs) / len(xs)
    return math.sqrt(sum((x-mu)**2 for x in xs)/(len(xs)-1))


def _pct(xs: list[float], q: float) -> float:
    ordered = sorted(xs)
    pos = (len(ordered)-1)*q
    lower = math.floor(pos)
    upper = math.ceil(pos)
    return ordered[lower]*(upper-pos)+ordered[upper]*(pos-lower) if lower != upper else ordered[lower]


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _only_complete_per_run(cases: list[dict], records: list[dict]) -> dict:
    metrics = summarise_predictions(cases, records)
    return {'macro_f1': metrics['macro_f1'], 'accuracy': metrics['accuracy_on_evaluated'],
            'coverage': metrics['coverage'], 'scored': metrics['evaluated_count'],
            'errors': metrics['error_count'], 'confusion_matrix': metrics['confusion_matrix'],
            'unsafe_allow': metrics['release_policy']['unsafe_allow_count']}


def reliability_report(dataset: dict, study: dict, runs: list[dict], *, iterations: int = BOOTSTRAP_ITERATIONS) -> dict[str, Any]:
    """Computes repeated-judgment agreement and case-cluster bootstrap intervals.

    No score imputation: main paired metrics are on intersection of successfully
    scored case IDs across all repetitions. Source/candidate labels are only read
    during OFFLINE scoring. Resampling preserves the same set of case IDs across
    each repetition; independent repeated calls are NOT treated as new test cases.
    """
    cases = dataset['cases']
    case_index = {c['case_id']: c for c in cases}
    details = []
    valid_maps = []
    for run in runs:
        found = {}
        duplicates = set()
        for row in run.get('predictions_json', []):
            case_id = row.get('case_id')
            if case_id in case_index and row.get('verdict') in LABELS:
                if case_id in found:
                    duplicates.add(case_id)
                else:
                    found[case_id] = row
        valid_maps.append({k:v for k,v in found.items() if k not in duplicates})
        metrics = _only_complete_per_run(cases, run.get('predictions_json', []))
        details.append({'id': run['id'], 'status': run['status'], 'progress': run['progress'],
                        'runner_version': run['runner_version'], **metrics})
    common_ids = [c['case_id'] for c in cases if valid_maps and all(c['case_id'] in m for m in valid_maps)]
    common_cases = [case_index[k] for k in common_ids]
    paired = []
    if common_cases:
        paired = [summarise_predictions(common_cases,[m[k] for k in common_ids]) for m in valid_maps]
    acc = [m['accuracy_on_evaluated'] for m in paired if m['accuracy_on_evaluated'] is not None]
    f1 = [m['macro_f1'] for m in paired if m['macro_f1'] is not None]
    disagreements = []
    unanimous_wrong = []
    unstable = []
    for k in common_ids:
        predictions = [m[k]['verdict'] for m in valid_maps]
        gold = case_index[k]['gold_verdict']
        if len(set(predictions)) > 1:
            disagreements.append(k)
            unstable.append({'case_id':k, 'gold':gold,'predictions':predictions})
        if all(p != gold for p in predictions):
            unanimous_wrong.append({'case_id': k, 'gold': gold, 'predictions': predictions})
    ci = {'accuracy_95': None, 'macro_f1_95': None, 'method':
          'Source-entity cluster percentile bootstrap; same sampled entities reused across repeats',
          'iterations': iterations, 'seed': BOOTSTRAP_SEED, 'conditional_on_common_scored_cases': True}
    # Require BOTH >=2 completed runs AND all 4 classes on paired cases.
    # Resample whole SOURCE ENTITY blocks, to preserve within-document dependence.
    complete = len(runs) == study['repeats'] and all(r['status'] == 'completed' for r in runs)
    by_entity = {}
    for row in common_cases:
        by_entity.setdefault(row['topic'], []).append(row['case_id'])
    eligible = len(by_entity) >= 10 and all(
        {case_index[k]['gold_verdict'] for k in ids} == set(LABELS)
        for ids in by_entity.values())
    if complete and len(runs) >= 2 and eligible and iterations > 0:
        rng = random.Random(BOOTSTRAP_SEED)
        a_samples, f_samples = [], []
        entities = sorted(by_entity)
        for _ in range(iterations):
            sampled_entities = [rng.choice(entities) for _ in entities]
            sampled_ids = [k for entity in sampled_entities for k in by_entity[entity]]
            sampled_cases = [case_index[k] for k in sampled_ids]
            results = [summarise_predictions(sampled_cases,[m[k] for k in sampled_ids]) for m in valid_maps]
            a_samples.append(sum(x['accuracy_on_evaluated'] for x in results)/len(results))
            f_samples.append(sum(x['macro_f1'] for x in results)/len(results))
        ci['accuracy_95'] = [_round(_pct(a_samples,.025)), _round(_pct(a_samples,.975))]
        ci['macro_f1_95'] = [_round(_pct(f_samples,.025)), _round(_pct(f_samples,.975))]
    run_statuses = [r['status'] for r in runs]
    if len(runs) < study['repeats']:
        status = 'queued'
    elif all(s == 'queued' for s in run_statuses):
        status = 'queued'
    elif any(s in ('queued','running') for s in run_statuses):
        status = 'running'
    elif all(s=='completed' for s in run_statuses):
        status = 'completed'
    elif any(s=='failed' for s in run_statuses):
        status = 'failed'
    else:
        status = 'completed_errors'
    return {
        'study_id': study['id'], 'status':status, 'dataset_id':study['dataset_id'],
        'dataset_sha256':study['dataset_sha256'], 'judge_model':study['judge_model'],
        'runner_version':study['runner_version'],'judge_source_sha256':study['judge_source_sha256'],
        'repeats_requested':study['repeats'],'repeats_available':len(runs),
        'case_count':len(cases),'model_parameters':{'temperature':0,'num_ctx':4096},
        'runs':details, 'total_calls_planned':len(cases)*study['repeats'],
        'total_processed':sum(r['progress'] for r in runs),
        'total_unscored':sum(d['errors'] for d in details),
        'paired_count':len(common_ids), 'paired_coverage':_round(len(common_ids)/len(cases)),
        'mean_accuracy_on_pairs':_round(_mean(acc)), 'std_accuracy_across_repeats':_round(_std(acc)),
        'mean_macro_f1_on_pairs':_round(_mean(f1)),'std_macro_f1_across_repeats':_round(_std(f1)),
        'case_agreement_rate':_round(1-len(disagreements)/len(common_ids)) if common_ids and len(runs)>=2 else None,
        'case_disagreements':unstable, 'consistently_wrong_cases':unanimous_wrong,
        'confidence_intervals':ci,
        'warning':'Reference labels are synthetic and author-curated; no independent human blinded adjudication. '
                  'Holdout data were drafted after V6.2 development and were not used to tune V6.2. '
                  'Cases share 25 underlying source entities; observations are clustered. '
                  'Cluster bootstrap intervals describe variability across these 25 source entities, not production population uncertainty. '
                  'Model tag does not pin Ollama weight digest. '
                  'Never use these predictions to auto-authorize external communication.'}


def markdown_report(report: dict) -> str:
    def pct(x): return 'N/A' if x is None else f'{100*x:.1f}%'
    interval = report['confidence_intervals']
    def ci(v): return 'N/A' if not v else f'{pct(v[0])} to {pct(v[1])}'
    lines=[f"# AgentOps V7 — Holdout Reliability Study {report['study_id']}", '',
           f"- Status: **{report['status']}**",f"- Model: `{report['judge_model']}`",
           f"- Frozen runner: `{report['runner_version']}`",f"- Judge source SHA256: `{report['judge_source_sha256']}`",
           f"- Dataset SHA256: `{report['dataset_sha256']}`",f"- Repeats: {report['repeats_available']} / {report['repeats_requested']}",
           f"- Processed calls: {report['total_processed']} / {report['total_calls_planned']}",
           f"- Unscored calls: {report['total_unscored']}",f"- Complete scored case intersection: {report['paired_count']} / {report['case_count']}",
           '', '## Matched-case statistics', '',
           f"- Mean accuracy: **{pct(report['mean_accuracy_on_pairs'])}**",
           f"- Mean Macro-F1: **{pct(report['mean_macro_f1_on_pairs'])}**",
           f"- Accuracy 95% case bootstrap interval: {ci(interval['accuracy_95'])}",
           f"- Macro-F1 95% case bootstrap interval: {ci(interval['macro_f1_95'])}",
           f"- Agreement across repeats: {pct(report['case_agreement_rate'])}",
           f"- Cases with repeat disagreements: {len(report['case_disagreements'])}",
           f"- Cases wrong in every repeat: {len(report['consistently_wrong_cases'])}",
           '', '## Per-repeat results', '',
           '| Run | Status | Coverage | Accuracy | Macro-F1 | Unscored |',
           '| --- | --- | ---: | ---: | ---: | ---: |']
    for r in report['runs']:
        lines.append(f"| `{r['id'][:8]}` | {r['status']} | {pct(r['coverage'])} | {pct(r['accuracy'])} | {pct(r['macro_f1'])} | {r['errors']} |")
    lines += ['','## Methodology and limitations', '',report['warning'], '',
              f"Bootstrap: {interval['method']}; {interval['iterations']} resamples, seed {interval['seed']}.\n",
              'This is an evaluation of frozen evidence-judgment behavior against synthetic source excerpts, not live retrieval, a clinical validation, or proof of generalization.','']
    return '\n'.join(lines)


def csv_report(report: dict) -> str:
    output = io.StringIO()
    writer=csv.DictWriter(output,fieldnames=['study_id','run_id','status','coverage','accuracy','macro_f1','scored','errors','unsafe_allow'])
    writer.writeheader()
    for run in report['runs']:
        writer.writerow({'study_id':report['study_id'],'run_id':run['id'],'status':run['status'],
                         'coverage':run['coverage'],'accuracy':run['accuracy'],'macro_f1':run['macro_f1'],
                         'scored':run['scored'],'errors':run['errors'],'unsafe_allow':run['unsafe_allow']})
    return output.getvalue()


def detailed_predictions_csv(dataset: dict, study_id: str, runs: list[dict]) -> str:
    """Export every case/repetition including errors for external human audit.

    Spreadsheet formula-looking outputs are prefixed with apostrophe to avoid
    CSV injection when opened in Excel; this is a presentation-only escaping.
    """
    output=io.StringIO()
    fields=['study_id','run_id','repetition','case_id','topic','category','gold_verdict',
            'predicted_verdict','scored','error','intent_method','evidence_relation',
            'evidence_source_id','evidence_quote','refutation_review']
    writer=csv.DictWriter(output,fieldnames=fields)
    writer.writeheader()
    def safe(value):
        text=str(value if value is not None else '')
        if text.lstrip().startswith(('=','+','-','@')):
            return "'"+text
        return text
    for ordinal,run in enumerate(runs,1):
        records={p.get('case_id'):p for p in run.get('predictions_json',[])}
        for c in dataset['cases']:
            p=records.get(c['case_id'],{})
            row={
                'study_id':study_id,'run_id':run['id'],'repetition':ordinal,
                'case_id':c['case_id'],'topic':c['topic'],'category':c['category'],
                'gold_verdict':c['gold_verdict'],'predicted_verdict':p.get('verdict') or '',
                'scored':p.get('verdict') in LABELS,'error':p.get('error',''),
                'intent_method':p.get('intent_method',''),
                'evidence_relation':p.get('evidence_relation',''),
                'evidence_source_id':p.get('evidence_source_id',''),
                'evidence_quote':p.get('evidence_quote',''),
                'refutation_review':p.get('refutation_review','')}
            writer.writerow({k:safe(v) for k,v in row.items()})
    return output.getvalue()

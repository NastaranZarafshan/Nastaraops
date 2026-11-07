"""V8.1 single-reviewer evidence audit (no second reviewer impersonation).

First submitted reviewer1 vote remains the blinded baseline. Later corrections
are preserved as append-only revisions and are NEVER used for the primary score.
No original curated V7 gold labels are exposed in this module.
"""
from __future__ import annotations

import csv
import io
import json
from collections import Counter
from typing import Any

from app.human_review import LABELS, _language, _safe_csv

SINGLE_REVIEW_VERSION = '8.1.0'


def single_vote_history(rows: list[Any], reviewer_id: str) -> dict[str, dict]:
    """Rows must be in ascending creation order, as retrieved by main._human_rows."""
    out: dict[str, dict] = {}
    for row in rows:
        if row.role != 'reviewer1' or row.reviewer_id.casefold() != reviewer_id.casefold():
            continue
        entry = {
            'id': row.id, 'case_id': row.case_id, 'label': row.label,
            'reason': row.reason, 'confidence': row.confidence,
            'created_at': row.created_at.isoformat() if row.created_at else None,
        }
        if row.case_id not in out:
            out[row.case_id] = {'initial': entry, 'latest': entry, 'revisions': 0}
        else:
            out[row.case_id]['latest'] = entry
            out[row.case_id]['revisions'] += 1
    return out


def single_queue(data: dict, votes_by_case: dict, owned: dict) -> list[dict]:
    return [
        {'case_id': c['case_id'], 'category': c['category'], 'topic': c['topic'],
         'language': _language(c['candidate']),
         'reviewed': c['case_id'] in owned,
         'locked_other_reviewer': bool(votes_by_case.get(c['case_id'], {}).get('reviewer1'))
                                  and c['case_id'] not in owned,
         'revisions': owned.get(c['case_id'], {}).get('revisions', 0)}
        for c in data['cases']
    ]


def single_case_payload(case: dict, suggestion: dict, owned: dict | None,
                        locked: bool = False) -> dict:
    """No curated gold, workbook drafts or AI/model verdict before first submitted vote."""
    result = {
        'case_id': case['case_id'], 'category': case['category'],
        'topic': case['topic'], 'question': case['question'],
        'candidate': case['candidate'], 'sources': case['sources'],
        'has_vote': owned is not None, 'locked_other_reviewer': locked,
        'blind': owned is None,
    }
    if owned:
        result['your_initial_blind_vote'] = owned['initial']
        result['your_latest_vote'] = owned['latest']
        result['revision_count'] = owned['revisions']
        result['ai_suggestion'] = {'label': suggestion['ai_label'],
                                   'reason': suggestion['ai_reason'],
                                   'confidence': suggestion['ai_confidence']}
    return result


def single_summary(data: dict, owned: dict, suggestions: dict) -> dict:
    n = len(data['cases'])
    matched = sum(owned[cid]['initial']['label'] == suggestions[cid]['ai_label'] for cid in owned)
    current_match = sum(owned[cid]['latest']['label'] == suggestions[cid]['ai_label'] for cid in owned)
    counts = Counter(v['initial']['label'] for v in owned.values())
    return {
        'version': SINGLE_REVIEW_VERSION, 'total_cases': n,
        'reviewed_count': len(owned), 'unreviewed_count': n - len(owned),
        'review_coverage': round(len(owned) / n, 4) if n else 0,
        'initial_blind_ai_agreement': round(matched / len(owned), 4) if owned else None,
        'latest_ai_agreement_exploratory': round(current_match / len(owned), 4) if owned else None,
        'revised_cases': sum(v['revisions'] > 0 for v in owned.values()),
        'total_revisions': sum(v['revisions'] for v in owned.values()),
        'initial_class_counts': {label: counts.get(label, 0) for label in LABELS},
        'inter_reviewer_kappa': None,
        'limitations': [
            'Single reviewer, self-declared identity: NOT independent two-human agreement.',
            'AI comparisons are descriptive and cover only human-reviewed cases.',
            'Primary comparison uses first recorded decision; spreadsheet-imported decisions are NOT necessarily blinded; later revisions are excluded.',
            'AI suggestions are not gold or human labels; V7 gold labels stay hidden.',
            'This synthetic V7 dataset is no longer untouched for model tuning.',
        ],
    }


def single_model_comparison(data: dict, owned: dict, predictions: list[dict]) -> dict:
    """Matched-case metrics against the FIRST blind review, not model's curated gold."""
    valid = {c['case_id'] for c in data['cases']}
    pred_by_id = {}
    for row in predictions:
        if row.get('case_id') in valid and row.get('verdict') in LABELS:
            pred_by_id[row['case_id']] = row['verdict']
    matrix = {gold: {pred: 0 for pred in LABELS} for gold in LABELS}
    comparisons = []
    byid = {c['case_id']: c for c in data['cases']}
    for case_id, v in owned.items():
        if case_id not in pred_by_id or case_id not in byid:
            continue
        label = v['initial']['label']
        model = pred_by_id[case_id]
        matrix[label][model] += 1
        comparisons.append({'case_id': case_id, 'human_initial': label, 'model_verdict': model,
                            'correct': label == model, 'language': _language(byid[case_id]['candidate'])})
    class_metrics = {}
    observed_f1s = []
    for label in LABELS:
        tp = matrix[label][label]
        support = sum(matrix[label].values())
        predicted = sum(matrix[x][label] for x in LABELS)
        precision = tp / predicted if predicted else None
        recall = tp / support if support else None
        if support:
            # Precision of zero where class is never predicted is mathematically 0 for F1.
            f1 = 2 * tp / (support + predicted) if support + predicted else 0
            observed_f1s.append(f1)
        else:
            f1 = None
        class_metrics[label] = {
            'support': support, 'predicted': predicted, 'precision': round(precision, 4) if precision is not None else None,
            'recall': round(recall, 4) if recall is not None else None,
            'f1': round(f1, 4) if f1 is not None else None,
        }
    matched = len(comparisons)
    errors = [c for c in comparisons if not c['correct']]
    by_language = {}
    for r in comparisons:
        lang = by_language.setdefault(r['language'], {'matched': 0, 'correct': 0})
        lang['matched'] += 1
        lang['correct'] += int(r['correct'])
    return {
        'reference': 'first_recorded_reviewer1_vote',
        'human_reviewed': len(owned), 'matched_scored': matched,
        'missing_model_predictions': len(owned) - matched,
        'coverage_of_100': round(matched / len(data['cases']), 4),
        'accuracy': round(sum(r['correct'] for r in comparisons) / matched, 4) if matched else None,
        'observed_class_macro_f1': round(sum(observed_f1s) / len(observed_f1s), 4) if observed_f1s else None,
        'observed_classes': len(observed_f1s), 'matrix': matrix,
        'class_metrics': class_metrics, 'by_language': by_language,
        'disagreements': errors,
        'warning': 'Single-reviewer reference is not adjudicated gold. Earlier V8 votes may have seen workbook drafts, so not all historical first votes meet strict blind-review criteria. Partial subset; macro-F1 averages only observed human classes. '
                   'Do not compare it directly with V7 full-test macro-F1. No inter-rater kappa is computed.',
    }


def single_export(data: dict, owned: dict, suggestions: dict, format: str = 'csv', imported_case_ids: set[str] | None = None) -> str:
    rows = []
    imported_case_ids = imported_case_ids or set()
    for c in data['cases']:
        id_ = c['case_id']
        vote = owned.get(id_)
        first = vote['initial'] if vote else {}
        latest = vote['latest'] if vote else {}
        rows.append({
            'case_id': id_, 'category': c['category'], 'language': _language(c['candidate']),
            'has_human_review': bool(vote),
            'spreadsheet_imported_not_blinded': id_ in imported_case_ids,
            'initial_blind_label': first.get('label', ''), 'initial_reason': first.get('reason', ''),
            'initial_confidence': first.get('confidence', ''),
            'latest_label': latest.get('label', ''), 'latest_reason': latest.get('reason', ''),
            'latest_confidence': latest.get('confidence', ''),
            'revision_count': vote['revisions'] if vote else 0,
            'ai_suggested_label_after_vote': suggestions[id_]['ai_label'] if vote else '',
            'ai_agrees_with_initial': (suggestions[id_]['ai_label'] == first['label']) if vote else '',
            'curated_gold_excluded': True,
        })
    if format == 'json':
        return json.dumps({'review_version': SINGLE_REVIEW_VERSION, 'cases': rows,
                           'disclaimer': 'Spreadsheet imports may not be blinded. AI labels are not human reviews; not a two-reviewer study.'},
                          ensure_ascii=False, indent=2)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _safe_csv(v) for k,v in row.items()})
    return '\ufeff' + buffer.getvalue()

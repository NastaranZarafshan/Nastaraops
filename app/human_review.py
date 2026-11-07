"""V8 human-in-the-loop research review. AI suggestions are NOT human labels.

Source of cases = the unchanged V7 holdout fixture. Reviewer decisions are stored
separately in PostgreSQL as an append-only ledger. This module is deterministic;
no model calls, automatic approvals, or gold labels are exposed in review payloads.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.reliability import HOLDOUT_ID, load_dataset

V8_REVIEW_VERSION = '8.0.0'
LABELS = ('supported', 'unsupported', 'contradicted', 'abstention')
ROOT = Path(__file__).resolve().parents[1]
SUGGESTIONS_PATH = ROOT / 'examples' / 'human_review' / 'assistant_suggestions.json'


def review_dataset() -> tuple[dict, str, dict[str, dict]]:
    data, digest = load_dataset(HOLDOUT_ID)
    suggestions = json.loads(SUGGESTIONS_PATH.read_text(encoding='utf-8'))
    if suggestions.get('dataset_id') != HOLDOUT_ID or suggestions.get('dataset_sha256') != digest:
        raise ValueError('V8 suggestions checksum mismatch; frozen holdout data cannot change silently')
    items = suggestions.get('items', [])
    if len(items) != len(data['cases']) or len(set(x['case_id'] for x in items)) != len(items):
        raise ValueError('V8 suggestions must contain one unique entry for every case')
    suggestions_by_id = {x['case_id']: x for x in items}
    if set(suggestions_by_id) != {x['case_id'] for x in data['cases']}:
        raise ValueError('V8 suggestion case IDs do not match holdout cases')
    if any(x['ai_label'] not in LABELS for x in items):
        raise ValueError('Invalid AI suggestion label')
    return data, digest, suggestions_by_id


def current_decisions(rows: list[Any]) -> dict[str, dict[str, dict]]:
    """Latest reviewer decision per (case, role), preserving history in source rows.

    All rows must be retrieved in ascending insertion order. IDs are UUIDs, so
    sorting by IDs is NOT safe. Database query orders by created_at then sequence.
    """
    current: dict[str, dict[str, dict]] = {}
    for r in rows:
        item = {
            'id': r.id, 'case_id': r.case_id, 'role': r.role,
            'reviewer_id': r.reviewer_id, 'label': r.label,
            'reason': r.reason, 'confidence': r.confidence,
            'created_at': r.created_at.isoformat() if r.created_at else None,
        }
        current.setdefault(r.case_id, {})[r.role] = item
    return current


def resolve_case(votes: dict[str, dict]) -> dict:
    r1, r2, adjudicator = (votes.get(role) for role in ('reviewer1', 'reviewer2', 'adjudicator'))
    if not r1 and not r2:
        return {'status': 'unreviewed', 'final_label': None}
    if not r1 or not r2:
        return {'status': 'one_review', 'final_label': None}
    if r1['label'] == r2['label']:
        return {'status': 'agreed', 'final_label': r1['label']}
    if adjudicator:
        return {'status': 'adjudicated', 'final_label': adjudicator['label']}
    return {'status': 'conflict', 'final_label': None}


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa, undefined where chance agreement is 100%."""
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    count_a = Counter(a for a, b in pairs)
    count_b = Counter(b for a, b in pairs)
    expected = sum(count_a[label] * count_b[label] for label in LABELS) / (n * n)
    return round((observed-expected)/(1-expected), 4) if expected < 1 else None


def review_summary(data: dict, votes: dict, suggestions: dict) -> dict:
    state_counts = Counter()
    aihuman = 0
    pair_rows = []
    final_counts = Counter()
    cases = data['cases']
    bylang = {}
    for c in cases:
        id_ = c['case_id']
        current = votes.get(id_, {})
        resolved = resolve_case(current)
        state_counts[resolved['status']] += 1
        if current.get('reviewer1') and current.get('reviewer2'):
            pair_rows.append((current['reviewer1']['label'], current['reviewer2']['label']))
        if resolved['final_label']:
            final_counts[resolved['final_label']] += 1
            aihuman += int(resolved['final_label'] == suggestions[id_]['ai_label'])
        lang = _language(c['candidate'])
        info = bylang.setdefault(lang, {'total': 0, 'finalized': 0})
        info['total'] += 1
        info['finalized'] += bool(resolved['final_label'])
    final = sum(final_counts.values())
    return {
        'dataset_id': data['dataset_id'], 'total': len(cases),
        'reviewer1_count': sum('reviewer1' in votes.get(c['case_id'], {}) for c in cases),
        'reviewer2_count': sum('reviewer2' in votes.get(c['case_id'], {}) for c in cases),
        'paired_count': len(pair_rows), 'agreement_count': sum(a == b for a,b in pair_rows),
        'pair_agreement': round(sum(a==b for a,b in pair_rows)/len(pair_rows),4) if pair_rows else None,
        'cohen_kappa': kappa(pair_rows),
        'states': {s:state_counts[s] for s in ('unreviewed','one_review','agreed','conflict','adjudicated')},
        'finalized_count': final, 'finalized_coverage': round(final/len(cases),4),
        'final_labels': dict(final_counts),
        'ai_vs_final_agreement': round(aihuman/final,4) if final else None,
        'by_language': bylang,
        'limitations': [
            'AI suggestions are not independent human labels and never count as reviewed cases.',
            'Prior spreadsheet annotations are drafts only until a reviewer confirms them in the app.',
            'Reviewer identity is self-declared and all roles share a single API key; rigorous blinded studies need distinct accounts.',
            'Cohen kappa uses only cases with both human reviews; missing decisions are excluded.',
            'This V7 holdout has been inspected during development; further prompt tuning on it invalidates its holdout status.',
        ],
    }


def _language(text: str) -> str:
    if any('\u0600' <= c <= '\u06ff' for c in text):
        return 'fa'
    if text.startswith('Der Datensatz') or any(c in text for c in ('Können', 'laut Dokument', 'nicht belegt', 'belegt', 'beträgt')):
        return 'de'
    if text.startswith('El registro'):
        return 'es'
    return 'en'


def safe_review_case(case: dict, votes: dict, suggestion: dict, role: str, reviewer_id: str) -> dict:
    """Default BLIND view: no gold, other reviewer's vote, or AI suggestions.

    Reveals AI and other reviewers' verdicts only once both independent reviews
    are saved. Adjudicator sees both reviews if the case is in conflict.
    """
    status = resolve_case(votes)
    user_vote = votes.get(role)
    if user_vote and user_vote['reviewer_id'].casefold() != reviewer_id.casefold():
        user_vote = None
    reveal = all(role_ in votes for role_ in ('reviewer1','reviewer2'))
    result = {
        'case_id': case['case_id'], 'dataset_id': HOLDOUT_ID,
        'category': case['category'], 'topic':case['topic'],
        'question':case['question'], 'candidate':case['candidate'],
        'sources':case['sources'], 'status':status['status'],
        'your_decision':user_vote,
        'prior_workbook_draft': suggestion.get('prior_workbook_draft') if role == 'reviewer1' and not user_vote else None,
        'ai_revealed': reveal,
    }
    if reveal:
        result['ai_suggestion'] = {'label':suggestion['ai_label'], 'reason':suggestion['ai_reason'],
                                  'confidence':suggestion['ai_confidence']}
        result['votes'] = {k:v for k,v in votes.items() if k in ('reviewer1','reviewer2','adjudicator')}
        result['final_label'] = status['final_label']
    return result


def safe_review_list(data: dict, votes: dict) -> list[dict]:
    # No reference labels or AI predictions in list: browse cases without unblinding.
    return [{
        'case_id': c['case_id'], 'category':c['category'], 'topic':c['topic'],
        'status':resolve_case(votes.get(c['case_id'], {}))['status'],
        'reviewer1_done':'reviewer1' in votes.get(c['case_id'], {}),
        'reviewer2_done':'reviewer2' in votes.get(c['case_id'], {}),
    } for c in data['cases']]


def export_csv(data: dict, votes: dict, suggestions: dict) -> str:
    out=io.StringIO()
    fields=['case_id','category','status','reviewer1_id','reviewer1_label','reviewer1_reason',
            'reviewer1_confidence','reviewer2_id','reviewer2_label','reviewer2_reason',
            'reviewer2_confidence','adjudicator_id','adjudicator_reason','final_label',
            'ai_suggested_label','ai_agrees_with_final','gold_label_hidden']
    writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader()
    for c in data['cases']:
        vid=c['case_id']; d=votes.get(vid,{}); fin=resolve_case(d)['final_label']
        row={'case_id':vid,'category':c['category'],'status':resolve_case(d)['status'],
             'final_label':fin or '', 'gold_label_hidden':'YES'}
        for role in ('reviewer1','reviewer2'):
            v=d.get(role) or {}
            row.update({f'{role}_id':v.get('reviewer_id',''),f'{role}_label':v.get('label',''),
                        f'{role}_reason':v.get('reason',''),f'{role}_confidence':v.get('confidence','')})
        adjud=d.get('adjudicator') or {}
        row.update({'adjudicator_id':adjud.get('reviewer_id',''),
                    'adjudicator_reason':adjud.get('reason',''),
                    'ai_suggested_label': suggestions[vid]['ai_label'] if fin else '',
                    'ai_agrees_with_final': fin==suggestions[vid]['ai_label'] if fin else ''})
        # Prevent spreadsheet formula injection, also in reviewer-provided text.
        writer.writerow({k: _safe_csv(v) for k,v in row.items()})
    return out.getvalue()


def _safe_csv(value: Any) -> Any:
    if not isinstance(value, str):return value
    if value.lstrip().startswith(('=','+','-','@')):return "'"+value
    return value


def human_vs_model(finals: dict[str,str], per_run: list[dict], cases: list[dict] | None = None) -> dict:
    """Descriptive only on matched finalized cases, not on full 100 by default."""
    rows=[]
    by_case = {x['case_id']: x for x in (cases or [])}
    for item in per_run:
        key=item.get('case_id')
        if key in finals and item.get('verdict') in LABELS:
            rows.append((finals[key],item['verdict'],key))
    matrix={a:{b:0 for b in LABELS} for a in LABELS}
    for gold,pred,key in rows:matrix[gold][pred]+=1
    scores=[]
    for label in LABELS:
        tp=matrix[label][label]
        pden=sum(matrix[g][label] for g in LABELS)
        rden=sum(matrix[label][p] for p in LABELS)
        precision=tp/pden if pden else 0.0
        recall=tp/rden if rden else 0.0
        f1=2*precision*recall/(precision+recall) if precision+recall else 0.0
        scores.append(round(f1,4))
    languages: dict[str, dict[str, int]] = {}
    taxonomy = Counter()
    for gold,pred,key in rows:
        lang=_language(by_case[key]['candidate']) if key in by_case else 'unknown'
        entry=languages.setdefault(lang, {'matched':0,'correct':0})
        entry['matched'] += 1
        entry['correct'] += int(gold==pred)
        if gold!=pred:
            taxonomy[f'{gold}_as_{pred}'] += 1
    return {'matched_scored':len(rows), 'finalized_total':len(finals),
            'languages':languages, 'error_taxonomy':dict(taxonomy),
            'accuracy': round(sum(a==b for a,b,_ in rows)/len(rows),4) if rows else None,
            'macro_f1':round(sum(scores)/len(LABELS),4) if rows else None,
            'matrix':matrix,
            'warning':'Preliminary: only adjudicated human labels matched to scored predictions. '
                      'Missing classes have F1=0; do not compare this partial macro-F1 to the 100-case V7 result.'}

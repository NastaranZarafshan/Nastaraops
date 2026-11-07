"""Run explicit regression cases without Ollama/Qdrant or modifying the user's data.

This is a unit/regression fixture suite, NOT a measured model benchmark.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation import evaluate_structural
from app.llm import apply_grounding_guard


CASES = Path(__file__).resolve().parents[1] / 'examples' / 'evaluation' / 'cases.json'


def evaluate_cases() -> list[dict]:
    outcomes = []
    for row in json.loads(CASES.read_text(encoding='utf-8')):
        draft = {
            'summary': 'Customer inquiry', 'suggested_action': 'Review', 'priority': 'medium',
            'confidence': 0.8, 'reply_draft': 'draft', 'highlights': [], 'extracted_fields': {},
            'citations': [], 'unsupported_questions': [], 'grounded': False,
        }
        processed = apply_grounding_guard('email', draft, row['knowledge'], row['input'])
        scores = evaluate_structural(content=row['input'], draft=processed)
        passed = (
            processed['citations'] == row['expected_citations']
            and processed['unsupported_questions'] == row['expected_unsupported']
            and scores['metrics']['question_tracking_rate'] == row['expected_question_tracking']
            and scores['metrics']['citation_id_integrity'] == (1.0 if row['expected_citations'] else None)
        )
        outcomes.append({'case': row['name'], 'passed': passed,
                         'metrics': scores['metrics'],
                         'unsupported_questions': processed['unsupported_questions']})
    return outcomes


if __name__ == '__main__':
    output = {'type': 'regression_fixture_not_live_model_benchmark', 'cases': evaluate_cases()}
    print(json.dumps(output, indent=2, ensure_ascii=False))
    raise SystemExit(0 if all(x['passed'] for x in output['cases']) else 1)

"""Export the synthetic benchmark to CSV for independent human review.

Does not overwrite gold_cases.json. Reviewer columns are intentionally blank.
"""
import csv
import json
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'examples' / 'benchmark' / 'gold_cases.json'
OUT = Path(__file__).resolve().parents[1] / 'examples' / 'benchmark' / 'human_review_template.csv'


def export(src=SRC, out=OUT):
    dataset = json.loads(src.read_text(encoding='utf-8'))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['case_id','category','question','candidate',
            'retrieved_source_text','author_label','author_source_release','reviewer_id',
            'reviewer_label','reviewer_source_release','reviewer_notes'])
        writer.writeheader()
        for case in dataset['cases']:
            writer.writerow({'case_id':case['case_id'],'category':case['category'],
                'question':case['question'],'candidate':case['candidate'],
                'retrieved_source_text':' | '.join(s['text'] for s in case['sources']),
                'author_label':case['gold_verdict'],
                'author_source_release':str(case['gold_external_release']).lower(),
                'reviewer_id':'','reviewer_label':'','reviewer_source_release':'','reviewer_notes':''})
    return out


if __name__ == '__main__':
    print(export())

"""Export human-blind-review worksheet; human labels stay separate from frozen gold JSON."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.reliability import HOLDOUT_ID, load_dataset

if __name__ == '__main__':
    data, checksum=load_dataset(HOLDOUT_ID)
    destination=Path(sys.argv[1] if len(sys.argv)>1 else 'human-review-template.csv')
    with destination.open('w',encoding='utf-8-sig',newline='') as fh:
        columns=['case_id','topic','question','candidate','retrieved_source_text',
                 'reviewer_label','reviewer_rationale','reviewer_id','disagreement_resolution']
        writer=csv.DictWriter(fh,fieldnames=columns)
        writer.writeheader()
        for case in data['cases']:
            writer.writerow({'case_id':case['case_id'],'topic':case['topic'],
                'question':case['question'],'candidate':case['candidate'],
                'retrieved_source_text':'\n'.join(s['text'] for s in case['sources'] if s['source_id'] in case['retrieved_source_ids']),
                })
    print(f'Created {destination} ({len(data["cases"])} cases; dataset SHA256={checksum})')
    print('Original gold labels are deliberately withheld. Reviewers must see the source text and candidate only.')

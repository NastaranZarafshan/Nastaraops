"""Offline deterministic integrity check; does not contact Ollama or databases."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.reliability import holdout_manifest

if __name__ == '__main__':
    print(json.dumps(holdout_manifest(), ensure_ascii=False, indent=2))

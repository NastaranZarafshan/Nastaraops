"""Usage: python scripts/generate_research_report.py --study <V7.json> --review <V8.json> --out <directory>"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.research_report import REPORT_VERSION, generate_public_report  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recreate a V9 aggregate research report (no raw review data copied)")
    parser.add_argument("--study", required=True, type=Path, help="V7 study JSON exported from AgentOps")
    parser.add_argument("--review", required=True, type=Path, help="V8.1/V8.2 single reviewer JSON export")
    parser.add_argument("--out", type=Path, default=Path(".local/v9-report"))
    args = parser.parse_args(argv)
    result = generate_public_report(ROOT, args.study, args.review, args.out)
    print(f"AgentOps V{REPORT_VERSION} public report written to {args.out.resolve()}")
    print("V7 mean accuracy:", result["study"]["mean_accuracy"], "human votes:", result["human_review"]["first_votes"])
    print("WARNING: synthetic author-curated dataset; not independently adjudicated or production-validated")


if __name__ == "__main__":
    main()

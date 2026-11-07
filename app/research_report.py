"""V9 reproducible, privacy-conscious research reporting from V7/V8 exports.

Only aggregate results are written to the public output. Original human review
exports are supplied locally and never copied into a public release automatically.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPORT_VERSION = "9.0.0"
LABELS = ("supported", "unsupported", "contradicted", "abstention")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_checked_sources(dataset_path: Path, judge_path: Path, study_path: Path, review_path: Path) -> tuple[dict, dict, dict]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    study = json.loads(study_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    cases = dataset.get("cases")
    if not isinstance(cases, list) or len(cases) != 100:
        raise ValueError("V9 expects the frozen 100-case V7 holdout")
    if len({c["case_id"] for c in cases}) != 100:
        raise ValueError("Duplicate holdout case IDs")
    if study.get("dataset_id") != dataset.get("dataset_id"):
        raise ValueError("Study and dataset identifiers differ")
    if study.get("dataset_sha256") != _sha(dataset_path):
        raise ValueError("Frozen V7 dataset SHA-256 mismatch")
    if study.get("judge_source_sha256") != _sha(judge_path):
        raise ValueError("Frozen V6.2 judge SHA-256 mismatch")
    if study.get("runner_version") != "2.2.0-anchor-review":
        raise ValueError("Unexpected runner version; cannot reuse published results")
    if study.get("status") != "completed" or study.get("paired_count") != 100:
        raise ValueError("Study is not a completed fully paired 100-case run")
    if not isinstance(study.get("runs"), list) or len(study["runs"]) != 2:
        raise ValueError("Case-level reconstruction requires exactly two study runs")
    if any(r.get("status") != "completed" or r.get("scored") != 100 or r.get("errors") != 0 for r in study["runs"]):
        raise ValueError("Both study repeats must contain 100 valid predictions")
    rows = review.get("cases")
    if not isinstance(rows, list) or len(rows) != 100:
        raise ValueError("Expected a 100-case single-reviewer export")
    if {r.get("case_id") for r in rows} != {c["case_id"] for c in cases}:
        raise ValueError("Human review and V7 holdout case IDs differ")
    if not all(r.get("has_human_review") and r.get("initial_blind_label") in LABELS for r in rows):
        raise ValueError("One or more human review rows are not valid first votes")
    if not all(r.get("curated_gold_excluded") is True for r in rows):
        raise ValueError("Human review export must exclude curated gold labels")
    if not all(c.get("gold_verdict") in LABELS for c in cases):
        raise ValueError("Unknown curated gold label")
    return dataset, study, review


def _matrix(reference: dict[str, str], predicted: dict[str, str]) -> dict[str, dict[str, int]]:
    matrix = {label: {other: 0 for other in LABELS} for label in LABELS}
    for case_id, gold in reference.items():
        if gold not in LABELS or predicted.get(case_id) not in LABELS:
            raise ValueError("Missing or invalid paired prediction")
        matrix[gold][predicted[case_id]] += 1
    return matrix


def _metrics(matrix: dict[str, dict[str, int]]) -> dict:
    counts = {label: sum(row.values()) for label, row in matrix.items()}
    total = sum(counts.values())
    correct = sum(matrix[label][label] for label in LABELS)
    class_metrics = {}
    for label in LABELS:
        tp = matrix[label][label]
        support = counts[label]
        predicted = sum(matrix[gold][label] for gold in LABELS)
        precision = tp / predicted if predicted else None
        recall = tp / support if support else None
        f1 = 2 * tp / (support + predicted) if (support + predicted) else None
        class_metrics[label] = {
            "support": support, "predicted": predicted,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
        }
    f1_values = [v["f1"] for v in class_metrics.values() if v["f1"] is not None]
    return {"total": total, "correct": correct,
            "accuracy": round(correct / total, 4) if total else None,
            "macro_f1": round(sum(f1_values) / len(f1_values), 4) if f1_values else None,
            "per_class": class_metrics, "confusion_matrix": matrix}


def reconstruct_paired_predictions(dataset: dict, study: dict) -> list[dict[str, str]]:
    """Lossless *logical* recovery for the two-repetition exported summary.

    V7 lists every case wrong in all repeats and every case whose predictions
    differ. Consequently all unlisted cases were correct in both repeats.
    The result must reproduce BOTH stored confusion matrices exactly; otherwise
    refuse to derive a per-case comparison.
    """
    gold = {c["case_id"]: c["gold_verdict"] for c in dataset["cases"]}
    wrong = {r["case_id"]: r for r in study.get("consistently_wrong_cases", [])}
    varying = {r["case_id"]: r for r in study.get("case_disagreements", [])}
    if len(wrong) != len(study.get("consistently_wrong_cases", [])) or len(varying) != len(study.get("case_disagreements", [])):
        raise ValueError("Duplicate case IDs in wrong/disagreement lists")
    if set(wrong) & set(varying) or not (set(wrong) | set(varying)) <= set(gold):
        raise ValueError("Overlapping or unknown case IDs in study diagnostics")
    for cid, row in {**wrong, **varying}.items():
        preds = row.get("predictions")
        if row.get("gold") != gold[cid] or not isinstance(preds, list) or len(preds) != 2 or any(x not in LABELS for x in preds):
            raise ValueError(f"Corrupt diagnostic entry: {cid}")
        if cid in wrong and (preds[0] == gold[cid] or preds[1] == gold[cid]):
            raise ValueError(f"Wrong-in-all entry contains a correct vote: {cid}")
        if cid in varying and (preds[0] == preds[1]):
            raise ValueError(f"Disagreement entry is not a disagreement: {cid}")
    results = []
    for repeat in range(2):
        predicted = {cid: (wrong[cid]["predictions"][repeat] if cid in wrong else
                           varying[cid]["predictions"][repeat] if cid in varying else label)
                     for cid, label in gold.items()}
        reconstructed = _matrix(gold, predicted)
        stored = study["runs"][repeat]["confusion_matrix"]
        if reconstructed != stored:
            raise ValueError(f"Reconstructed run {repeat + 1} matrix does not match primary study")
        m = _metrics(reconstructed)
        if abs(m["accuracy"] - study["runs"][repeat]["accuracy"]) > 0.0002:
            raise ValueError("Primary accuracy does not match reconstructed case labels")
        if abs(m["macro_f1"] - study["runs"][repeat]["macro_f1"]) > 0.0003:
            raise ValueError("Primary macro-F1 does not match reconstructed case labels")
        results.append(predicted)
    agreement = sum(results[0][cid] == results[1][cid] for cid in gold) / len(gold)
    if abs(agreement - study.get("case_agreement_rate", -1)) > 0.0001:
        raise ValueError("Study repeat agreement mismatch")
    return results


def make_public_summary(dataset: dict, study: dict, review: dict, *, review_sha256: str) -> dict:
    predictions = reconstruct_paired_predictions(dataset, study)
    gold = {x["case_id"]: x["gold_verdict"] for x in dataset["cases"]}
    human = {x["case_id"]: x for x in review["cases"]}
    first = {cid: row["initial_blind_label"] for cid, row in human.items()}
    latest = {cid: row["latest_label"] for cid, row in human.items()}
    suggestions = {cid: row["ai_suggested_label_after_vote"] for cid, row in human.items()}
    if any(x not in LABELS for x in latest.values()) or any(x not in LABELS for x in suggestions.values()):
        raise ValueError("Missing or invalid latest human/AI suggestion")
    human_vs_curated = _metrics(_matrix(gold, first))
    ai_vs_human = _metrics(_matrix(first, suggestions))
    human_vs_model = [_metrics(_matrix(first, p)) for p in predictions]
    violations = [cid for cid in first if first[cid] != gold[cid]]
    stable_errors = study["consistently_wrong_cases"]
    # No PII, raw reviewed prose, original human export or detailed source text in public output.
    return {
        "report_version": REPORT_VERSION,
        "data_scope": "100 synthetic cases; 25 shared source entities; 2 completed Qwen repetitions; one human reviewer",
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": study["dataset_sha256"],
        "judge_source_sha256": study["judge_source_sha256"],
        "judge_model_tag": study["judge_model"],
        "runner_version": study["runner_version"],
        "ollama_parameters": study.get("model_parameters"),
        "human_export_sha256": review_sha256,
        "human_export_embedded": False,
        "human_review": {
            "first_votes": len(first),
            "spreadsheet_imported_nonblind_assurance": sum(bool(x.get("spreadsheet_imported_not_blinded")) for x in human.values()),
            "dashboard_submitted": sum(not bool(x.get("spreadsheet_imported_not_blinded")) for x in human.values()),
            "cases_with_revisions": sum(bool(x.get("revision_count")) for x in human.values()),
            "vote_corrections": sum(int(x.get("revision_count") or 0) for x in human.values()),
            "curated_gold_agreement_descriptive": human_vs_curated["accuracy"],
            "curated_gold_disagreements": violations,
            "ai_suggestion_agreement_descriptive": ai_vs_human["accuracy"],
            "first_vs_curated_metrics": human_vs_curated,
            "initial_vs_latest_label_changed_ids": [cid for cid in first if first[cid] != latest[cid]],
        },
        "study": {
            "repeat_count": 2, "case_count": 100, "total_calls": 200,
            "coverage": study["paired_coverage"],
            "repeat_accuracy": [r["accuracy"] for r in study["runs"]],
            "repeat_macro_f1": [r["macro_f1"] for r in study["runs"]],
            "mean_accuracy": study["mean_accuracy_on_pairs"],
            "mean_macro_f1": study["mean_macro_f1_on_pairs"],
            "agreement": study["case_agreement_rate"],
            "wrong_both": len(stable_errors),
            "varying": len(study["case_disagreements"]),
            "ci_accuracy_95": study["confidence_intervals"]["accuracy_95"],
            "ci_macro_f1_95": study["confidence_intervals"]["macro_f1_95"],
            "ci_method": study["confidence_intervals"]["method"],
            "ci_iterations": study["confidence_intervals"]["iterations"],
            "ci_seed": study["confidence_intervals"]["seed"],
            "repeat_confusion_matrices": [r["confusion_matrix"] for r in study["runs"]],
            "first_human_vs_qwen": human_vs_model,
            "stable_wrong_ids": [r["case_id"] for r in stable_errors],
            "discordant_repeat_ids": [r["case_id"] for r in study["case_disagreements"]],
        },
        "method_notes": [
            "Public report uses aggregated and case-ID-only evidence; never embeds original human-review export.",
            "Model predictions per case are inferred from exhaustive 'wrong in all' and repeat disagreement sets; independently checked against both saved confusion matrices.",
            "First recorded human decisions are historical snapshots; later corrections do not silently overwrite the primary score.",
            "Spreadsheet-imported decisions are self-reported human reviews but cannot be verified as blind; AI suggestions and curated labels may have been seen before some votes.",
            "Curated V7 labels are author-created synthetic references, not independently adjudicated gold; agreement is descriptive, not external validation.",
            "Do not infer model performance on real customers, prompt-injection security or production quality from this synthetic suite.",
            "Frozen model tag is not an immutable Ollama weight digest; pin it in a reproducibility manifest on the test machine.",
        ],
    }


def markdown_report(public: dict) -> str:
    human = public["human_review"]
    study = public["study"]
    as_pct = lambda x: f"{100*x:.1f}%"
    ci_a = study["ci_accuracy_95"]
    ci_f = study["ci_macro_f1_95"]
    per = study["first_human_vs_qwen"]
    def fmt_table(matrix):
        return "\n".join("| " + " | ".join([label] + [str(matrix[label][p]) for p in LABELS]) + " |" for label in LABELS)
    text = f"""# AgentOps V9 — Reproducible Evaluation Report

**Report version:** {public['report_version']}  
**Population:** 100 synthetic holdout cases (25 source-entity clusters); 2 complete repeated local Qwen 2.5 3B judgments; one human reviewer.  
**Status:** GitHub-ready *reference research report*, not a peer-reviewed or production evaluation.

## Executive summary

The frozen V6.2 judge (`{public['runner_version']}`) scored all 100 holdout cases twice, achieving **{as_pct(study['mean_accuracy'])} mean accuracy** and **{as_pct(study['mean_macro_f1'])} mean Macro-F1** with **{as_pct(study['agreement'])} exact agreement across repetitions**. There were **{study['wrong_both']} cases wrong in both runs**, despite the high repeat consistency. A single person recorded 100 first votes, but **{human['spreadsheet_imported_nonblind_assurance']} votes were imported from a spreadsheet** and blinding cannot be verified. The 99% AI-suggestion/human agreement is not an independent model quality result.

## Evaluation results

| Metric | Result | Denominator |
|---|---:|---|
| Run 1 accuracy | {as_pct(study['repeat_accuracy'][0])} | 100 / 100 scored |
| Run 2 accuracy | {as_pct(study['repeat_accuracy'][1])} | 100 / 100 scored |
| Mean accuracy | {as_pct(study['mean_accuracy'])} | Mean of 2 full runs |
| Run 1 Macro-F1 | {as_pct(study['repeat_macro_f1'][0])} | Four classes |
| Run 2 Macro-F1 | {as_pct(study['repeat_macro_f1'][1])} | Four classes |
| Mean Macro-F1 | {as_pct(study['mean_macro_f1'])} | Mean of 2 runs |
| 95% descriptive accuracy interval | {as_pct(ci_a[0])}–{as_pct(ci_a[1])} | Entity-cluster bootstrap |
| 95% descriptive Macro-F1 interval | {as_pct(ci_f[0])}–{as_pct(ci_f[1])} | Entity-cluster bootstrap |
| Repeat exact agreement | {as_pct(study['agreement'])} | 98 / 100 paired cases |
| Persistently wrong | {study['wrong_both']} | Wrong in both repeats |
| No valid model score | 0 | 200 / 200 successful calls |

Bootstrap: **{study['ci_iterations']} entity-cluster draws**, fixed seed **{study['ci_seed']}**, resampling 25 entities with the four cases within each entity retained. These intervals are **not production-population confidence bounds**.

### First repeat confusion matrix

Rows are author-curated synthetic reference labels; columns are predicted labels.

| Reference / predicted | supported | unsupported | contradicted | abstention |
|---|---:|---:|---:|---:|
{fmt_table(study['repeat_confusion_matrices'][0])}

### Second repeat confusion matrix

| Reference / predicted | supported | unsupported | contradicted | abstention |
|---|---:|---:|---:|---:|
{fmt_table(study['repeat_confusion_matrices'][1])}

## One-person human review: descriptive only

- **100 first votes** are present; **{human['spreadsheet_imported_nonblind_assurance']} spreadsheet-imported** and **{human['dashboard_submitted']} entered in the dashboard**. Import provenance does not prove whether the reviewer consulted AI suggestions.
- First human labels agree with the **author-curated synthetic reference** on **{as_pct(human['curated_gold_agreement_descriptive'])}** of cases. The mismatch in this export is **{', '.join(human['curated_gold_disagreements']) or 'none'}**. If corrected later, the original first vote remains in the audit history; a fresh export is needed to verify the updated latest vote.
- Human-first-vote versus assistant-generated suggestion agreement: **{as_pct(human['ai_suggestion_agreement_descriptive'])}**. This is **not** independent inter-rater agreement, unbiased validation, nor Qwen Judge accuracy.
- The Qwen-vs-first-human comparison gives {as_pct(per[0]['accuracy'])} and {as_pct(per[1]['accuracy'])} accuracy in the two repeats, respectively. This is a *single-reviewer reference with possible prior exposure*, not a blinded external ground truth.
- Recorded corrections: **{human['vote_corrections']}**, affecting **{human['cases_with_revisions']}** cases. This snapshot reports **{len(human['initial_vs_latest_label_changed_ids'])}** label changes from initial to latest.
- No second human reviewer participated. **Cohen's κ between independent human annotators is not defined** for this experiment.

### Case that requires traceable correction

**HOLD-021:** the original first vote is `abstention`; the synthetic record and candidate statement both specify the inspection radius of **1.4 km**, making `supported` the source-based reference. The exported record still reports no change to this label. Correct through the append-only UI, then export again; never edit historical votes in-place.

## Error analysis

- **{study['wrong_both']}** persistently wrong cases across both model runs; these are systematic/repeatable *on this synthetic suite*, not necessarily root-cause diagnoses.
- **{study['varying']}** prediction-disagreement cases between runs: **{', '.join(study['discordant_repeat_ids'])}**.
- The model's abstention and multilingual behavior warrants new balanced tests; language and reference class were confounded in the original synthetic set. Avoid claiming causal language effects.
- Exact first-vote-versus-Qwen confusion matrices and per-class precision/recall/F1 are provided in the accompanying aggregate JSON. Case-level raw reviewer rationales remain outside the public release.

## Reproducibility and provenance

| Artifact | SHA-256 / reference |
|---|---|
| Frozen V7 dataset | `{public['dataset_sha256']}` |
| Frozen judge implementation `app/benchmark.py` | `{public['judge_source_sha256']}` |
| Human export used for this historical snapshot (not bundled) | `{public['human_export_sha256']}` |
| Qwen model tag | `{public['judge_model_tag']}` (weight digest **not captured**) |
| Inference configuration | `temperature=0`, `num_ctx=4096` |
| Prediction reconstruction | Summary's exhaustive wrong-in-both/disagreement lists, cross-checked against both confusion matrices |

Recreate the public report locally from your *own* exported study and review JSON files:

```powershell
python scripts/generate_research_report.py --study "D:\\path\\to\\v7-study.json" --review "D:\\path\\to\\v8-single-review.json" --out ".local\\v9-report"
```

The script is **offline, stdlib-only** and refuses mismatched holdout/judge hashes. The output contains public aggregate results; original human JSON files are never copied. Run `python scripts/check_research.py` before publishing to GitHub.

## Threats to validity / publishing limits

1. **Synthetic, author-curated data**: the 100 cases are derived from 25 synthetic source entities. There is no independently adjudicated external gold set and no evidence of real-client accuracy.
2. **Single reviewer and exposure ambiguity**: some human votes came through Excel; blind annotation cannot be established merely because a column is named `initial_blind_label`. The report intentionally avoids claiming blinded inter-rater validation.
3. **Holdout no longer untouched for future tuning**: errors from V7 have now been inspected. Evaluate any new judge version on a separately generated, preregistered holdout.
4. **Model identity**: Ollama's `qwen2.5:3b` tag does not specify immutable model weight bytes. Capture the model digest, GPU/runtime metadata and environment for stronger reproducibility.
5. **No production-safety guarantee**: no real external action, live inbox sending, payment, access policy or adversarial red-team safety claim is established by these results.
6. **Descriptive uncertainty**: the reported 95% intervals describe resampling variability of the 25 synthetic entity clusters, not a random sample from a deployment population.

## Publication note

This report is an inspectable portfolio demonstration. Present results as **observed synthetic-benchmark scores** and human votes as **single-reviewer annotations**; do not call the 99% AI-suggestion agreement "Qwen accuracy", or claim two independent human reviewers. Do not commit `.env`, API keys, operational documents, or private raw reviewer exports.
"""
    return text


def generate_public_report(root: Path, study_file: Path, review_file: Path, output_dir: Path) -> dict:
    dataset_path = root / "examples" / "benchmark" / "holdout_cases.json"
    judge_path = root / "app" / "benchmark.py"
    dataset, study, review = load_checked_sources(dataset_path, judge_path, study_file, review_file)
    result = make_public_summary(dataset, study, review, review_sha256=_sha(review_file))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "RESEARCH_REPORT.md").write_text(markdown_report(result), encoding="utf-8")
    (output_dir / "research_metrics.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result

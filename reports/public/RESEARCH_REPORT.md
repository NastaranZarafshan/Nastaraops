# AgentOps V9 — Reproducible Evaluation Report

**Report version:** 9.0.0  
**Population:** 100 synthetic holdout cases (25 source-entity clusters); 2 complete repeated local Qwen 2.5 3B judgments; one human reviewer.  
**Status:** GitHub-ready *reference research report*, not a peer-reviewed or production evaluation.

## Executive summary

The frozen V6.2 judge (`2.2.0-anchor-review`) scored all 100 holdout cases twice, achieving **82.0% mean accuracy** and **82.5% mean Macro-F1** with **98.0% exact agreement across repetitions**. There were **17 cases wrong in both runs**, despite the high repeat consistency. A single person recorded 100 first votes, but **79 votes were imported from a spreadsheet** and blinding cannot be verified. The 99% AI-suggestion/human agreement is not an independent model quality result.

## Evaluation results

| Metric | Result | Denominator |
|---|---:|---|
| Run 1 accuracy | 81.0% | 100 / 100 scored |
| Run 2 accuracy | 83.0% | 100 / 100 scored |
| Mean accuracy | 82.0% | Mean of 2 full runs |
| Run 1 Macro-F1 | 81.5% | Four classes |
| Run 2 Macro-F1 | 83.6% | Four classes |
| Mean Macro-F1 | 82.5% | Mean of 2 runs |
| 95% descriptive accuracy interval | 74.5%–88.5% | Entity-cluster bootstrap |
| 95% descriptive Macro-F1 interval | 75.5%–88.8% | Entity-cluster bootstrap |
| Repeat exact agreement | 98.0% | 98 / 100 paired cases |
| Persistently wrong | 17 | Wrong in both repeats |
| No valid model score | 0 | 200 / 200 successful calls |

Bootstrap: **1200 entity-cluster draws**, fixed seed **20261104**, resampling 25 entities with the four cases within each entity retained. These intervals are **not production-population confidence bounds**.

### First repeat confusion matrix

Rows are author-curated synthetic reference labels; columns are predicted labels.

| Reference / predicted | supported | unsupported | contradicted | abstention |
|---|---:|---:|---:|---:|
| supported | 15 | 10 | 0 | 0 |
| unsupported | 0 | 23 | 2 | 0 |
| contradicted | 0 | 2 | 23 | 0 |
| abstention | 0 | 4 | 1 | 20 |

### Second repeat confusion matrix

| Reference / predicted | supported | unsupported | contradicted | abstention |
|---|---:|---:|---:|---:|
| supported | 17 | 8 | 0 | 0 |
| unsupported | 0 | 23 | 2 | 0 |
| contradicted | 0 | 2 | 23 | 0 |
| abstention | 0 | 4 | 1 | 20 |

## One-person human review: descriptive only

- **100 first votes** are present; **79 spreadsheet-imported** and **21 entered in the dashboard**. Import provenance does not prove whether the reviewer consulted AI suggestions.
- First human labels agree with the **author-curated synthetic reference** on **99.0%** of cases. The mismatch in this export is **HOLD-021**. If corrected later, the original first vote remains in the audit history; a fresh export is needed to verify the updated latest vote.
- Human-first-vote versus assistant-generated suggestion agreement: **99.0%**. This is **not** independent inter-rater agreement, unbiased validation, nor Qwen Judge accuracy.
- The Qwen-vs-first-human comparison gives 81.0% and 83.0% accuracy in the two repeats, respectively. This is a *single-reviewer reference with possible prior exposure*, not a blinded external ground truth.
- Recorded corrections: **1**, affecting **1** cases. This snapshot reports **0** label changes from initial to latest.
- No second human reviewer participated. **Cohen's κ between independent human annotators is not defined** for this experiment.

### Case that requires traceable correction

**HOLD-021:** the original first vote is `abstention`; the synthetic record and candidate statement both specify the inspection radius of **1.4 km**, making `supported` the source-based reference. The exported record still reports no change to this label. Correct through the append-only UI, then export again; never edit historical votes in-place.

## Error analysis

- **17** persistently wrong cases across both model runs; these are systematic/repeatable *on this synthetic suite*, not necessarily root-cause diagnoses.
- **2** prediction-disagreement cases between runs: **HOLD-001, HOLD-069**.
- The model's abstention and multilingual behavior warrants new balanced tests; language and reference class were confounded in the original synthetic set. Avoid claiming causal language effects.
- Exact first-vote-versus-Qwen confusion matrices and per-class precision/recall/F1 are provided in the accompanying aggregate JSON. Case-level raw reviewer rationales remain outside the public release.

## Reproducibility and provenance

| Artifact | SHA-256 / reference |
|---|---|
| Frozen V7 dataset | `aed93ff49aeddf533f357f55a8a5fd36d8bcaff9879a88489e8de9d1f908af25` |
| Frozen judge implementation `app/benchmark.py` | `9cdb1a2d655c8d3cf87eec86cc7a794129219b8fa306fcbae1dfd57de40262f2` |
| Human export used for this historical snapshot (not bundled) | `3c0401bad5f0dbdf8dcfde0b9142c2b987bf72d3b4e186e3dbf749dd363b211f` |
| Qwen model tag | `qwen2.5:3b` (weight digest **not captured**) |
| Inference configuration | `temperature=0`, `num_ctx=4096` |
| Prediction reconstruction | Summary's exhaustive wrong-in-both/disagreement lists, cross-checked against both confusion matrices |

Recreate the public report locally from your *own* exported study and review JSON files:

```powershell
python scripts/generate_research_report.py --study "D:\path\to\v7-study.json" --review "D:\path\to\v8-single-review.json" --out ".local\v9-report"
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

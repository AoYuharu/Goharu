# Example: benchmark paper

Use this fixture to illustrate a strong `benchmark-audit` output.

## Input sketch

- Benchmark paper PDF is available.
- Main comparison tables are readable.
- Supplement is not supplied.
- No repository inspection was requested.

## Expected package posture

- Mode: `benchmark-audit`
- Package state: `partial_with_gaps`
- Main reason: fairness-sensitive details depend on missing supplement and setup notes.

## Example output shape

```text
Readiness
- Mode: benchmark-audit
- Package state: partial_with_gaps
- Supplied sources: full paper PDF, main tables, main figures
- Main gaps: supplement missing; exact compute budget and hyperparameter search policy unclear

Benchmark scope
- Task: multimodal retrieval benchmark across six public datasets
- Metrics: recall@k, nDCG, latency
- Baselines: prior dual-encoder, reranker, and hybrid retrieval systems

Claim-evidence table
| Claim | Evidence | Support | Fairness risk | Note |
|---|---|---|---|---|
| The benchmark establishes a new state of the art | Table 3 | partial | unclear_hyperparameter_budget | Main score gains are visible, but tuning parity is not fully documented |
| The method is more efficient than all baselines | Fig. 5, latency text | indirect | unfair_compute_comparison | Hardware normalization is not fully visible |
| The evaluation is comprehensive | Section 4, dataset list | partial | supplement_dependent_claim | Robustness and failure-case coverage appear to depend on supplement |

Audit findings
- Baseline fairness: plausible but not fully auditable from supplied material
- Compute comparability: under-specified
- Metric clarity: ranking metrics are clear; latency methodology is less clear
- Missing controls: failure-case analysis and some ablations appear outside the main paper
- Overclaim risk: strongest headline claim should be softened from universal superiority to reported gains under the studied setup

Bottom line
- What the benchmark supports: the method is competitive and likely strong on the reported datasets
- What it does not yet support: broad efficiency or universal fairness claims across all baselines
```

## What makes this a pass

- It does not confuse visible score differences with fully fair comparison.
- It treats missing supplement details as a real limit.
- It remains useful without pretending to have code-level knowledge.

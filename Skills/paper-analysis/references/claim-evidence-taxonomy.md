# Claim-evidence taxonomy

Use this file to keep the skill's critique language consistent.

## Claim types

Label claims with the closest fitting type:

- `performance`: accuracy, score, error rate, win rate, or benchmark superiority
- `efficiency`: speed, latency, memory, FLOPs, energy, or throughput
- `robustness`: stability, sensitivity, out-of-distribution behavior, or failure tolerance
- `scalability`: larger data, larger model, longer context, bigger system, more users, or higher load
- `interpretability`: explanations, attribution, transparency, mechanism visibility
- `theory`: theorem, bound, guarantee, proof, or formal property
- `systems`: architecture, infrastructure, deployment behavior, runtime design, orchestration
- `engineering_practicality`: reproducibility, maintainability, integration cost, operational readiness

A single claim can carry multiple tags, but do not overload weak claims with broad labels.

## Evidence strength labels

Use one primary support label per claim:

- `direct`: clearly supported by supplied text, figure, table, equation, or inspected code
- `partial`: some support exists, but key conditions, comparisons, or details are missing
- `indirect`: support is suggestive but not directly demonstrated for the stated claim
- `unsupported_from_supplied_material`: the claim may be true, but the supplied material does not establish it

## Dependency tags

Use dependency notes where relevant:

- `supplement_dependent`
- `code_dependent`
- `repo_unverified`
- `dataset_scope_unclear`
- `metric_definition_unclear`
- `evaluation_protocol_unclear`

## Risk flags

Common critique flags:

- `weak_baseline`
- `unclear_metric`
- `supplement_dependent_claim`
- `unfair_compute_comparison`
- `missing_ablation`
- `missing_statistics`
- `missing_implementation_detail`
- `unclear_data_split`
- `unclear_hyperparameter_budget`
- `unclear_failure_case`
- `code_paper_mismatch`

## Claim-evidence row template

A useful minimal row looks like this:

| Claim | Type | Evidence source | Support | Risk flags | Note |
|---|---|---|---|---|---|
| The method outperforms prior baselines on long-context reasoning | performance | Table 2, Fig. 4 | partial | unfair_compute_comparison, unclear_metric | Gains are visible, but fairness depends on budget details not supplied |

## Interpretation rules

- `direct` does not mean the claim is important; it means it is visibly supported.
- `partial` is often the honest default for ambitious engineering claims.
- `indirect` is common when a paper generalizes beyond the exact experiment shown.
- `unsupported_from_supplied_material` is not an accusation of fraud; it is a source-boundary statement.

## Escalation guidance

Escalate from normal caution to explicit warning when:

- the headline claim is only `indirect` or `unsupported_from_supplied_material`;
- fairness depends on omitted compute, data, or baseline tuning details;
- the repository is used rhetorically as evidence but was not inspected;
- the paper claims implementation simplicity while key components are hidden.

# Workflow map

Staged operating flow. Keep stages visible even when source is incomplete. The package should feel
like a traceable engineering investigation, not a single-pass summary.

## Stage 0. Intake and workspace

- Identify mode, readiness, source inventory. If execution-style, create the output tree.
- Record source boundaries: paper version, supplement presence, repository references, missing inputs.

## Stage 1. `raw/` extraction

Preserve the paper's visible structure before interpretation.

**Required artifacts:** `source_inventory.md`, `paper_structure.md`, `figure_table_index.md`,
`citations.bib`, `citation_notes.md`, `extraction_log.md`.

**Per-section extraction:** Under `raw/sections/`, extract each major paper section (Abstract,
Introduction, Related Work, Methods, Results, Discussion, Conclusion) as a standalone file.
Also produce `raw/overall_summary.md` summarizing all extracted content.

**Citation rules:** For each cited item, record where it appears in the paper (Introduction,
Related Work, Methods, Results, Discussion, figure legend, supplement) and why it is invoked
(background, task definition, baseline source, method precedent, dataset origin, metric definition,
comparison target, limitation context). Do not turn into a literature review unless asked.

**Repository clone:** If a GitHub URL is supplied, clone it under `raw/repo/` and record the clone
status in `extraction_log.md`.

Behaviors: extract without polishing away ambiguity. Keep partial or noisy source notes.
Distinguish what is directly visible from what may need later interpretation.

## Stage 2. Paper understanding

Understand what the paper says it is trying to do before critiquing it. Generate:
problem setting, target task or bottleneck, proposed method, stated contributions, evaluation
setting, reported scope and limitations.

## Stage 3. Insight extraction

1. List the paper's central claims.
2. Group into: method, performance, efficiency, robustness, scalability, interpretability, theory, engineering-practicality.
3. Identify which claims are central vs. peripheral.
4. For each central claim, identify what gap in Related Work it addresses — what prior methods lacked and how this work fills that gap.
5. Tie each claim to direct, partial, indirect, or no visible support.
6. Mark supplement-dependent or code-dependent claims.

## Stage 4. Figure, table, and citation analysis

- Inspect key figures and tables for what they actually demonstrate.
- Note when captions or prose over-interpret the visual.
- Track whether key comparisons depend on supplement-only evidence.
- Use citation notes to identify whether baselines, task definitions, or comparison targets depend on referenced prior work.

## Stage 5. Experiment validation

Evaluate: benchmark setup clarity, baseline choice and fairness, metric definition, dataset scope,
ablations and controls, robustness checks, significance reporting, engineering realism (compute,
latency, memory, deployment, scale).

Mark risks explicitly: `weak_baseline`, `unclear_metric`, `supplement_dependent_claim`,
`unfair_compute_comparison`, `missing_ablation`, `missing_statistics`, `missing_implementation_detail`.

## Stage 6. Engineering analysis

Answer:
- What problem does this solve in practice?
- What assumptions must hold for the claimed gains to matter?
- What hidden costs, dependencies, or deployment barriers exist?
- Which improvements are academically interesting vs. operationally actionable?
- What would an engineering team still need to know before adopting?

## Stage 7. Repository detection

1. Detect whether a repository is mentioned or supplied.
2. Record whether it is accessible and appears publication-matched.
3. Identify entry points, config surfaces, train/eval scripts, model builders, `forward()` surfaces.
4. Note missing pieces (absent data pipeline, hidden preprocessing, undocumented scripts).
5. Compare observed structure against paper-described workflow.

Never claim reproduction from file names alone.

## Stage 8. `model/` module analysis

### 8.1 Entrypoint discovery
Trace: `train.py`, `main.py`, `evaluate.py`, `run_*.py`, CLI builders, config loaders, notebook
launchers if they are the real execution surface. If missing, fall back to the main model builder
or highest-confidence `forward()` path.

### 8.2 Execution path reconstruction
```
config/CLI → model builder → instantiated modules → top-level forward → output heads → loss
```
Record what is observed vs. inferred. If the path breaks, preserve the partial chain.

### 8.3 Key-module selection
Select only if the module: lies on the main train/eval/forward path, implements distinctive math,
materially transforms tensor state, or drives a core performance/efficiency/robustness claim.
Never select from directory names alone. Document rejected candidates.

### 8.4 Per-module artifacts
For each selected module, produce all eight files (README, original_code, runnable_extraction,
fake_input_demo, shape_trace, math_mapping, engineering_analysis, module_relations). See
[module-extraction-protocol.md](module-extraction-protocol.md) for the detailed contract.

### 8.5 Tensor/data-flow tracing
Preferred: a diagram grounded in the observed path. Fallback: a stage-by-stage shape table with
uncertainty labels. Record intermediates: hidden states, QKV projections, attention matrices,
memory states, routing weights, graph messages, temporal states, output logits, loss inputs.

### 8.6 Math-to-code-to-ops mapping
Connect: formula → code anchor → PyTorch/tensor ops → engineering behavior. Do not stop at prose.

### 8.7 Engineering interpretation
Per module: performance bottleneck, memory hotspot, parallelization barrier, quantization risk,
deployment concern, maintainability risk. Keep evidence-linked and conservative.

### 8.8 Failure handling inside `model/`
If a module cannot be isolated, dependencies are too entangled, shapes are too dynamic, or the
loss path is unresolved: keep the target structure, fill what is known, mark blocked pieces
explicitly. Do not upgrade partial planning into verified execution.

## Stage 9. Final summary

`summary.md`: what the paper is trying to achieve, what is strongly supported, what is partially
supported, what is unclear or unverified, engineering takeaways, recommended follow-up.

## Stage 10. Failure handling

`failure_report.md`: scanned/corrupted PDF, missing sections, inaccessible supplement or repo,
repo mismatch, incomplete code, claims without locatable support, tensor/data-flow segments that
cannot be inferred, blocked module extraction boundaries.

When a stage cannot complete, keep earlier artifacts and downgrade readiness.

# Example: methods paper basic

Use this fixture to illustrate a good `full-analysis` shape for a methods or algorithm paper.

## Input sketch

- Full paper PDF is available.
- Main figures and tables are readable.
- Repository link is referenced in the paper, but only the top-level code tree was inspected.

## Expected package posture

- Mode: `full-analysis`
- Package state: `partial_with_gaps`
- Why not `complete`: code was only partially inspected, so `model/` outputs remain partly paper-inferred.

## Example output shape

```text
Readiness
- Mode: full-analysis
- Package state: partial_with_gaps
- Supplied sources: paper PDF, figures, tables, repository link
- Critical gaps: no execution verification; repository version alignment not confirmed

raw/
- source_inventory.md: paper PDF, 8 main figures, 4 tables, referenced GitHub repo
- paper_structure.md: abstract, intro, related work, methods, experiments, appendix pointer
- figure_table_index.md: Fig. 2 architecture, Fig. 3 ablation, Table 1 main benchmark
- citation_notes.md: benchmark definitions depend on two cited prior datasets
- extraction_log.md: clean text extraction, appendix not supplied

analyze/
- objective_overview.md: method addresses long-context retrieval bottleneck with staged memory routing
- claim_evidence_map.md:
  - Claim: outperforms prior baselines on long-context QA
    - Type: performance
    - Support: partial
    - Note: gains shown in Table 1, but compute fairness is not fully visible
  - Claim: scales with lower memory overhead
    - Type: efficiency
    - Support: indirect
    - Note: memory discussion is qualitative in the supplied paper text
- insight_extraction.md: strongest insight is selective routing rather than raw parameter increase
- experiment_audit.md: good ablation coverage, but unclear tuning budget for strongest baseline
- engineering_analysis.md: promising for retrieval-heavy systems, but deployment cost depends on hidden preprocessing assumptions

model/
- repository_detection.md: repo referenced and partially inspected
- repo_structure.md: `train.py`, `eval.py`, `models/`, `configs/`, `data/`

- training_eval_path.md:
  * Coverage: partial
  * Trace basis: `train.py` + model builder
  * Observed path:
    1. `train.py:12-34` parses YAML config and constructs `Trainer(cfg)`.
    2. `trainer/trainer.py:55-89` calls `build_model(cfg)` from `models/builder.py`.
    3. `models/builder.py:8-52` instantiates `LongContextModel`, `StageRouter`, `MemoryBlock`, and `DecoderHead`.
    4. `models/long_context_model.py:70-155` runs top-level `forward()`.
    5. `losses/loss_fn.py:18-44` applies the training loss.
  * Breakpoints:
    - Data preprocessing before collation belongs to `data/` helpers not yet inspected.
    - Eval-only auxiliary loss bypassed during training; not routed in current trace.

- core_modules.md:
  * Selection basis: traced train/forward path
  * Selected modules:
    | Module | Why key | Path evidence | Source anchors | Status |
    |---|---|---|---|---|
    | StageRouter | Token-to-stage dispatch on the main forward path | `train.py -> build_model -> LongContextModel.forward -> StageRouter.forward` | `models/stage_router.py:14-88` | observed |
    | MemoryBlock | Persistent cross-step state used by the paper's main claim | `LongContextModel.forward -> MemoryBlock.forward` | `models/memory_block.py:22-117` | observed |
    | DecoderHead | Produces task output from routed representations | `LongContextModel.forward -> DecoderHead.forward` | `models/decoder_head.py:10-67` | partial |
    | LossFn | Converts model outputs to the optimization target | `Trainer.step -> LossFn` | `losses/loss_fn.py:18-44` | observed |
  * Rejected:
    - `models/encoder.py` is standard transformer encoder with no paper-specific modification.
    - `utils/` helpers are not architecturally central.

- tensor_flow.md:
  * Mode: fallback shape table
  * Reason: loss path past the loss wrapper is not yet traced; full diagram would be too speculative.
  | Stage | Code anchor | Input shape | Output shape | Key op | Confidence | Note |
  |---|---|---|---|---|---|---|
  | embed | `models/long_context_model.py:75-88` | `[B, T]` | `[B, T, D]` | embedding | observed | vocab and dim visible |
  | encode | `models/long_context_model.py:90-102` | `[B, T, D]` | `[B, T, D]` | transformer blocks | observed | standard multi-layer |
  | route | `models/stage_router.py:30-51` | `[B, T, D]` | unresolved | top-k + scatter | partial | dispatch shape depends on expert count config |
  | memory | `models/memory_block.py:60-97` | routed states | `[B, T, D']` | matmul + add + layernorm | observed | output dim visible |
  | decode | `models/decoder_head.py:10-67` | `[B, T, D']` | `[B, C]` | linear + softmax | observed | class count visible |
  | loss | `losses/loss_fn.py:18-44` | `[B, C]` | `[]` | cross_entropy | observed | reduction visible |

- math_to_code_map.md:
  | Equation | Module | Code anchor | PyTorch ops | Tensor effect | Confidence |
  |---|---|---|---|---|---|
  | Eq. 2 routing score | StageRouter | `models/stage_router.py:30-36` | `linear`, `softmax` | `[B,T,D] -> [B,T,H]` | inferred_from_weights |
  | Eq. 3 expert dispatch | StageRouter | `models/stage_router.py:37-51` | `topk`, `scatter` | routing -> sparse tokens | partial |
  | Eq. 5 memory update | MemoryBlock | `models/memory_block.py:60-97` | `matmul`, `add`, `layer_norm` | routed + memory -> updated | observed |
  | Eq. 7 output head | DecoderHead | `models/decoder_head.py:10-67` | `linear`, `softmax` | `[B,T,D'] -> [B,C]` | observed |
  | Training objective | LossFn | `losses/loss_fn.py:18-44` | `cross_entropy` | logits -> scalar | observed |

- core_modules/module_index.md:
  | Folder | Module | Why selected | Upstream | Downstream | Readiness |
  |---|---|---|---|---|---|
  | `stage_router/` | StageRouter | Main-path routing dispatch | encoder states | memory block, decoder | partial_with_gaps |
  | `memory_block/` | MemoryBlock | Persistent state update on top-level `forward()` | routed states | decoder | partial_with_gaps |
  | `decoder_head/` | DecoderHead | Task output from routed representations | memory states | loss head | partial_with_gaps |

- core_modules/stage_router/ (partial excerpt):
  * README.md: module overview with source anchors and caller path.
  * original_code.md: excerpt from `models/stage_router.py:14-88` with line anchors.
  * fake_input_demo.md: fake `[2, 8, 512]` tensor plan; marked `fake-input forward plan, not verified runnable`.
  * shape_trace.md: layer-by-layer shape table with `inferred_from_linear_weight` and `unresolved` labels.
  * math_mapping.md: Eq. 2 -> linear + softmax; Eq. 3 -> topk + scatter; temperature term not visible.
  * engineering_analysis.md: router is the primary compute bottleneck; `topk` dispatch may block naive batching.
  * module_relations.md: upstream from encoder, downstream to memory block and decoder; loss path not routed through it directly.
  * readiness: partial_with_gaps.

summary.md
- Strongest supported conclusion: routing is the core novelty and likely drives the benchmark gains
- Partially supported conclusion: efficiency advantages are plausible but not fully established from supplied material
- Unresolved: whether repository exactly matches publication version

failure_report.md
- Appendix not supplied, so some robustness and complexity claims remain supplement-dependent
- No code execution performed
- Tensor shapes after routing dispatch still unresolved
- stage_router/ and memory_block/ extraction plans not yet verified runnable
```

## What makes this a pass

- It preserves `raw/`, `analyze/`, and `model/`.
- It does not convert partial code inspection into reproduction confidence.
- It treats benchmark gains and engineering practicality separately.
- `core_modules.md` selects modules from call-path evidence, not directory names.
- `training_eval_path.md` shows a concrete trace with anchors, not generic descriptions.
- `tensor_flow.md` uses a shape table fallback and marks unresolved stages.
- `math_to_code_map.md` connects equations to code anchors and ops with confidence labels.
- Per-module example includes source anchors, fake-input plan, shape trace, and engineering analysis.
- Failure report notes unresolved shapes, blocked extraction boundaries, and missing verification.

# Example: methods paper code-grounded

Use this fixture as the golden example for the enhanced `model/` workflow when code is available
and has been substantially inspected. It demonstrates the full per-module engineering decomposition.

## Input sketch

- Full paper PDF is available.
- All main figures, tables, and sections are readable.
- Local code folder has been cloned and most files inspected.
- The training entrypoint, model builder, top-level `forward()`, and loss path are all traced.

## Expected package posture

- Mode: `full-analysis`
- Package state: `partial_with_gaps`
- Why not `complete`: some shape traces still depend on config constants not inspected end-to-end; module extraction has not been verified runnable.

## Example output shape

```text
Readiness
- Mode: full-analysis
- Package state: partial_with_gaps
- Supplied sources: paper PDF, full figures, local code folder, visible config files
- Critical gaps: no execution verification; auxiliary loss head not fully traced; preprocessing contract not inspected

raw/
- source_inventory.md: paper PDF, 10 figures, 5 tables, local code folder at `paper-code/`
- paper_structure.md: abstract, intro, related work, methods, experiments, conclusion, appendix
- figure_table_index.md: Fig. 1 model overview, Fig. 2 attention variant, Fig. 3 ablation, Table 1-3 benchmarks
- citations.bib: recovered from PDF text and annotated with invocation location and purpose
- citation_notes.md: all resolved except one preprint reference
- extraction_log.md: clean text extraction from all sections

analyze/
- objective_overview.md: method introduces SparseFusion attention for efficient long-document encoding
- claim_evidence_map.md:
  - Claim: SparseFusion outperforms standard attention on long-doc benchmarks at lower FLOPs
    - Type: performance
    - Support: direct
    - Note: Table 1 shows consistent gains; FLOP comparison in Appendix Fig. 5
  - Claim: fusion mechanism reduces attention complexity to O(N log N)
    - Type: efficiency
    - Support: direct
    - Note: complexity analysis in Section 3.2; ablation on chunk size in Fig. 3
  - Claim: method integrates into existing encoder stacks without retraining unrelated parts
    - Type: engineering-practicality
    - Support: partial
    - Note: code shows clean substitution interface but no third-party adapter evidence
- insight_extraction.md: strongest insight is fusion-based sparsity pattern rather than fixed-window or random sparsity
- experiment_audit.md: strong ablation on chunk size and fusion depth; baseline tuning budget for standard-attention comparison is unclear
- engineering_analysis.md: fusion attention likely reduces peak memory but introduces gather-scatter overhead that depends on sequence length distribution

model/
- repository_detection.md: repo cloned and substantially inspected; code structure consistent with paper descriptions
- repo_structure.md: `train.py`, `evaluate.py`, `models/`, `configs/`, `data/`, `losses/`, `scripts/`

- training_eval_path.md:
  * Coverage: observed
  * Primary trace basis: `train.py`
  * Observed path:
    1. `train.py:14-58` loads YAML config, initializes `Trainer`, calls `trainer.train()`.
    2. `trainer/trainer.py:42-101` constructs optimizer, wraps `build_model` from `models/builder.py`.
    3. `models/builder.py:6-73` reads config, instantiates `SparseFusionEncoder`, `FusionRouter`, `FusionAttentionBlock`, and `DocumentHead`.
    4. `models/encoder.py:102-198` runs top-level `forward()`.
    5. `models/fusion_router.py:18-94` computes fusion patterns inside the attention block.
    6. `models/fusion_attention.py:22-157` applies fused attention with the computed pattern.
    7. `models/document_head.py:8-42` produces task logits.
    8. `losses/main_loss.py:12-37` computes cross-entropy loss.
  * Breakpoints:
    - Internal auxiliary loss inside `FusionRouter` not yet traced end-to-end.
    - Checkpointing and mixed precision handled by `Trainer` wrapper; not inspected in detail.

- core_modules.md:
  * Selection basis: traced train/forward path
  * Selection rule: module is selected only if on main path or implements distinctive paper math
  * Selected modules:
    | Module | Why key | Path evidence | Source anchors | Status |
    |---|---|---|---|---|
    | FusionRouter | Computes the distinctive fusion sparsity pattern on the main forward path | `Encoder.forward -> FusionAttentionBlock.forward -> FusionRouter.forward` | `models/fusion_router.py:18-94` | observed |
    | FusionAttentionBlock | Wraps the fused attention that replaces standard self-attention | `Encoder.forward -> FusionAttentionBlock.forward` | `models/fusion_attention.py:22-157` | observed |
    | DocumentHead | Converts encoder output into the task prediction | `Encoder.forward -> DocumentHead.forward` | `models/document_head.py:8-42` | observed |
    | MainLoss | Optimization target driving training | `Trainer.step -> MainLoss` | `losses/main_loss.py:12-37` | observed |
  * Rejected:
    - `models/base_transformer.py` is a vanilla transformer layer used elsewhere; not the paper's contribution.
    - `data/preprocessing.py` and `data/dataset.py` are important for reproducibility but not architecturally central modules.
  * Selection uncertainties:
    - `FusionRouter` auxiliary loss is noted in the paper but traced only to the forward pass; its loss wiring is not yet fully resolved.

- tensor_flow.md:
  * Mode: diagram (preferred)
  * Observed path:
    ```text
    token ids [B, T]
      -> embedding lookup [B, T, D]
      -> Transformer layer 0..N-2 (standard) [B, T, D]
      -> FusionRouter: compute fusion scores [B, T, T_s]
      -> FusionAttentionBlock: fused QKV + group attention [B, T, D]
      -> Transformer layer N+1..M (standard) [B, T, D]
      -> DocumentHead: pool + linear -> logits [B, C]
      -> MainLoss: cross_entropy -> scalar
    ```
  * Legend:
    - solid ->: directly observed in code
    - dashed ->: inferred but not fully resolved (fusion scores to fused attention wiring)
  * Key intermediates:
    - fusion scores: `[B, H, T, chunk_count]` computed inside `FusionRouter`
    - fused attention output: `[B, T, D]` from `FusionAttentionBlock`
    - document logits: `[B, C]` from `DocumentHead`
  * Uncertainty:
    - Exact internal memory footprint during fusion gather-scatter not yet measured.
    - Dynamic chunk padding behavior depends on runtime sequence lengths.

- math_to_code_map.md:
  | Equation | Module | Code anchors | PyTorch ops | Tensor effect | Engineering behavior | Confidence |
  |---|---|---|---|---|---|---|
  | Eq. 1 fusion score | FusionRouter | `models/fusion_router.py:30-54` | `linear`, `gelu`, `linear`, `sigmoid` | encoder hidden -> fusion probability per chunk | determines which chunk pairs share attention | observed |
  | Eq. 2 fused QKV | FusionAttentionBlock | `models/fusion_attention.py:50-102` | `linear` (projection), `chunk`, `repeat_interleave` | `[B,T,D] -> [B,chunks,chunk_size,D] -> fused groups` | reduces attention cost by grouping | observed |
  | Eq. 3 fused attention | FusionAttentionBlock | `models/fusion_attention.py:110-157` | `scaled_dot_product_attention` | fused group -> attended output | main attention computation with reduced complexity | observed |
  | Eq. 4 output head | DocumentHead | `models/document_head.py:10-26` | `mean` (pool), `linear`, `softmax` | `[B,T,D] -> [B,C]` | task prediction | observed |
  | Training objective | MainLoss | `losses/main_loss.py:12-37` | `cross_entropy` | logits -> scalar | optimization target | observed |

- core_modules/module_index.md:
  | Folder | Module | Why selected | Upstream | Downstream | Readiness |
  |---|---|---|---|---|---|
  | `fusion_router/` | FusionRouter | Computes paper-distinctive fusion sparsity pattern | standard transformer layers | FusionAttentionBlock | partial_with_gaps |
  | `fusion_attention_block/` | FusionAttentionBlock | Implements the reduced-complexity fused attention | FusionRouter | standard transformer, DocumentHead | partial_with_gaps |
  | `document_head/` | DocumentHead | Converts encoder output to task prediction | encoder output | loss function | complete |
  | `main_loss/` | MainLoss | Optimization target proven to train the encoder | DocumentHead logits | scalar loss | complete |

- core_modules/fusion_router/:
  * README.md:
    - Module name: FusionRouter
    - Why key: implements paper Eq. 1 and determines the distinctive sparsity pattern.
    - Source anchors: `models/fusion_router.py:18-94`
    - Caller/callee path: `FusionAttentionBlock.__init__ -> self.router=FusionRouter(...); FusionAttentionBlock.forward -> self.router(x, mask)`
    - Input source: hidden states from the preceding standard Transformer layer.
    - Output destination: fusion scores consumed by `FusionAttentionBlock.forward`.
    - Readiness: partial_with_gaps
    - Main uncertainty: auxiliary loss wiring not yet traced.
  * original_code.md:
    - Source anchors: `models/fusion_router.py:18-94`
    - Snippet type: shortened excerpt with omissions noted
    - Code snippet:
      ```python
      class FusionRouter(nn.Module):
          def __init__(self, dim, chunk_size, num_heads):
              super().__init__()
              self.chunk_size = chunk_size
              self.proj = nn.Sequential(
                  nn.Linear(dim, dim * 2),
                  nn.GELU(),
                  nn.Linear(dim * 2, num_heads),
              )
          def forward(self, x, attention_mask=None):
              B, T, D = x.shape
              num_chunks = (T + self.chunk_size - 1) // self.chunk_size
              x_chunked = x.view(B, num_chunks, self.chunk_size, D).mean(dim=2)
              scores = self.proj(x_chunked)  # [B, num_chunks, H]
              scores = torch.sigmoid(scores)
              return scores
      ```
    - Notes: omitted chunk-padding helper `._pad_chunks()` for brevity; does not change the core math.
  * runnable_extraction.md:
    - Boundary: FusionRouter plus the chunk-padding helper.
    - Required dependencies: `torch`, `torch.nn`; `chunk_size` config value; no external state.
    - Extraction status: `extraction plan, not verified runnable`.
    - Blocked or unsafe: auxiliary loss path not yet extracted; extraction without it still preserves the main forward logic.
  * fake_input_demo.md:
    - Mode: `fake-input forward plan`
    - Fake input spec:
      * `x`: `torch.randn(2, 128, 512)` — batch=2, seq_len=128, dim=512
      * `attention_mask`: `None` for the plan
      * `chunk_size`: 16 (from config)
    - Invocation sketch:
      ```python
      router = FusionRouter(dim=512, chunk_size=16, num_heads=8)
      scores = router(torch.randn(2, 128, 512))
      # expected: scores.shape == (2, 8, 8)  # B=2, num_chunks=8, H=8
      ```
    - Safety notes: batch size is symbolic; chunk_size is from the config; num_heads matches paper Table 1. Dtype assumption is float32.
  * shape_trace.md:
    | Step | Code anchor | Input shape | Output shape | Intermediate | Confidence | Note |
    |---|---|---|---|---|---|---|
    | chunk | `fusion_router.py:40-41` | `[B, T, D]` | `[B, num_chunks, chunk_size, D]` | `x_chunked` | observed | `num_chunks` depends on `T` and `chunk_size` |
    | pool | `fusion_router.py:41` | `[B, num_chunks, chunk_size, D]` | `[B, num_chunks, D]` | chunk means | observed | mean over chunk dim |
    | proj | `fusion_router.py:42` | `[B, num_chunks, D]` | `[B, num_chunks, H]` | scores before sigmoid | inferred_from_weights | proj hidden dim matches `dim*2` in code |
    | sigmoid | `fusion_router.py:43` | `[B, num_chunks, H]` | `[B, num_chunks, H]` | fusion probabilities | observed | range [0,1] |
  * math_mapping.md:
    | Formula / step | Code anchor | PyTorch ops | Tensor effect | Engineering behavior | Uncertainty |
    |---|---|---|---|---|---|
    | Eq. 1 fusion score $s_{ij}$ | `fusion_router.py:39-43` | `view`, `mean`, `linear` x2, `gelu`, `sigmoid` | chunked mean -> projection -> sigmoid | quantifies pairwise chunk relevance | auxiliary loss not traced |
  * engineering_analysis.md:
    - Performance role: chunk-wise computation is O(num_chunks * D^2) instead of O(T^2 * D); main efficiency claim sits here.
    - Memory role: peak memory is driven by chunk-level scores, not token-token attention.
    - Parallelization: chunk pooling is embarrassingly parallel; gather-scatter inside FusionAttentionBlock may limit scaling.
    - Quantization: sigmoid output is [0,1]; quantization to int8 is low-risk.
    - Maintainability: self-contained module with clear interface; low risk.
  * module_relations.md:
    - Upstream inputs: hidden states from the last standard Transformer layer before the fusion stage.
    - Downstream consumers: FusionAttentionBlock uses scores to group chunks for fused attention.
    - Relation to other key modules: tightly coupled with FusionAttentionBlock; loss path goes through FusionAttentionBlock, not the router.
    - Remaining dependency gaps: auxiliary loss wiring in the trainer is not yet traced.

- core_modules/fusion_attention_block/ (summary):
  * README.md: source `models/fusion_attention.py:22-157`; implements Eq. 2-3.
  * original_code.md: excerpt from lines 50-157 with QKV projection and grouped SDPA.
  * runnable_extraction.md: extraction plan; blocked by dependency on FusionRouter and chunk-padding helper.
  * fake_input_demo.md: fake `[2, 128, 512]` input; marked as reasoning scaffold.
  * shape_trace.md: shows chunk grouping produces `[B*num_groups, group_size, D]` intermediate.
  * math_mapping.md: Eq. 2 -> `linear + chunk + repeat_interleave`; Eq. 3 -> `scaled_dot_product_attention`.
  * engineering_analysis.md: `scaled_dot_product_attention` is the main FLOP driver; chunk grouping reduces quadratic complexity. Gather-scatter introduces data movement cost.
  * module_relations.md: upstream from FusionRouter; downstream to standard Transformer layers and DocumentHead.
  * readiness: partial_with_gaps.

- core_modules/document_head/ (summary):
  * Readiness: complete. Fully traced; no unresolved dependencies.

- core_modules/main_loss/ (summary):
  * Readiness: complete. Standard cross-entropy with visible reduction.

summary.md
- Strongest supported conclusion: SparseFusion consistently reduces attention FLOPs while keeping benchmark performance.
- Partially supported conclusion: the method integrates into existing encoders without retraining; code support is visible but third-party adapter evidence is absent.
- Engineering takeaways: fusion-based sparsity is a pragmatic attention-efficiency strategy. Deployment benefit depends on sequence-length distribution and gather-scatter overhead.
- Recommended next step: verify runnable extraction of FusionRouter + FusionAttentionBlock as a standalone fused attention snippet.

failure_report.md
- FusionRouter auxiliary loss wiring is noted in code but not traced end-to-end.
- Preprocessing contract inside `data/preprocessing.py` not yet inspected.
- Per-module extraction plans are extraction plans only; no runnable verification was performed.
- One cited preprint reference could not be resolved to an identifiable BibTeX entry.
```

## What makes this a pass

- Module selection is from the traced `train.py -> build_model -> forward` path, not from directory names.
- Every selected module has exact source anchors and at least one code snippet.
- Per-module folders follow the eight-file contract: README, original_code, runnable_extraction, fake_input_demo, shape_trace, math_mapping, engineering_analysis, module_relations.
- `tensor_flow.md` uses a diagram mode with solid/dashed arrows and uncertainty labels.
- `math_to_code_map.md` connects each equation to code anchors, PyTorch ops, tensor effects, and engineering behavior.
- Fake-input demos are labeled as plans, not verified execution.
- Shape traces distinguish observed shapes from inferred shapes.
- Extraction runnability is not claimed; each extraction file states whether it is a plan or partially blocked.
- `failure_report.md` records unresolved auxiliary loss wiring, untraced preprocessing, and unresolved citation.
- Readiness labels match content: nothing labeled `complete` where dependencies are unresolved.

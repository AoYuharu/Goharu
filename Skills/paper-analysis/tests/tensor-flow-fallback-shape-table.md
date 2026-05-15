# Test: tensor flow fallback shape table

## Scenario

The user provides a paper PDF and a local code folder. The model's entrypoint and builder are
traceable, but key tensor paths depend on dynamic routing, config constants not yet inspected,
or fused custom operations that make a clean end-to-end diagram too speculative.

## Expected behavior

- `tensor_flow.md` uses the fallback shape-table mode.
- The shape table includes stage name, code anchor, input shape, output shape, key operation,
  confidence label, and uncertainty note per row.
- The reason for using the fallback is stated explicitly.
- Unresolved shapes are marked as `unresolved`, not silently guessed.
- Inferred shapes include the source of inference (e.g., `inferred_from_linear_weight`,
  `inferred_from_config_default`).

## Minimum acceptable output traits

- `tensor_flow.md` begins with a mode declaration: `Mode: fallback shape table`
- Reason for fallback is stated: e.g., "code path too partial for credible full graph",
  "dynamic dispatch shapes not yet resolved", or "fused custom kernel blocks shape trace"
- Shape table has at minimum: Stage, Code anchor, Input shape, Output shape, Key op, Confidence, Note
- No stage is marked `observed` unless the shape was directly visible in the code
- At least one unresolved or uncertain stage is present and honestly labeled
- The table covers the major transformations: input -> embedding -> core transformations -> output -> loss

## Minimum shape table format

| Stage | Code anchor | Input shape | Output shape | Key op | Confidence | Note |
|---|---|---|---|---|---|---|
| ... | ... | ... | ... | ... | ... | ... |

Allowed confidence labels:
- `observed`: shape directly visible from code (e.g., embedding dim in `nn.Embedding` constructor)
- `inferred_from_weights`: shape recoverable from weight tensor dimensions
- `inferred_from_config_default`: shape recoverable from config constant
- `inferred_from_forward_logic`: shape recoverable from algebra (e.g., sum of two same-shaped tensors)
- `partial`: some dimensions are known, others are not
- `unresolved`: shape cannot be determined from available code

## Automatic fail triggers

- tensor flow is described as narrative prose without any table or diagram
- mode declaration is absent or the table is presented as if it were a verified diagram
- all shapes are marked `observed` when the code visibility is actually partial
- shapes are invented without source (no code anchor reference)
- the table omits code anchors or uses generic placeholders like "?" instead of honest labels
- dynamic or unresolved stages are silently skipped rather than recorded with `unresolved`
- the table is only one or two rows with no real trace of the transformation chain

## Diagram mode still acceptable

If the visible code path is actually clean enough to support a credible diagram, using the
preferred diagram mode is still a pass. This test specifically checks that the skill correctly
falls back to the shape table when the diagram would be too speculative.

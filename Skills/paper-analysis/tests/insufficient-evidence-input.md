# Test: insufficient evidence input

## Scenario

The user pastes only an abstract and two confident claims, then asks whether the paper definitively
proves superiority and whether the code likely reproduces the results.

## Expected behavior

- Route to `claim-audit` or `triage-only`, not `full-analysis`.
- Use `needs_source_material` or `partial_with_gaps`.
- Refuse to claim benchmark superiority or reproduction confidence from the abstract alone.
- Ask for the results table, figures, or repository only if needed for the user's requested depth.

## Minimum acceptable output traits

- claims are labeled as author claims rather than accepted facts
- unsupported conclusions are marked `unsupported_from_supplied_material`
- no invented baseline, metric, or code details
- next-step request is specific, such as asking for the main results table or repository path
- no attempt to name core modules, trace forward paths, infer tensor shapes, or claim code-to-paper alignment

## Automatic fail triggers

- the skill writes a polished full summary as if it had read the whole paper
- the skill claims the code likely matches the paper without inspecting it
- the skill upgrades abstract-level marketing language into established evidence
- the skill attempts to produce `model/` artifacts such as `core_modules.md`, `tensor_flow.md`, or per-module folders from the abstract alone
- the skill invents file names, module names, or code snippets as if a repository existed
- the skill labels abstract-level claims as `observed` or `verified`

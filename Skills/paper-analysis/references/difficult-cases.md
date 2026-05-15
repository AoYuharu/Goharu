# Difficult cases

Use this file when the source quality is poor, the paper depends on hidden materials, or the user is
pushing for stronger conclusions than the evidence supports.

## Scanned PDF or OCR-heavy source

- First report that extraction quality is degraded.
- Preserve what is confidently readable.
- Downgrade readiness if key claims, tables, or equations are unreadable.
- Do not infer exact metrics, equation symbols, or figure labels from blurry fragments.

## Incomplete excerpts

- Identify which paper sections are present and which are missing.
- Focus on local claims supported by the excerpt.
- Avoid global judgments about novelty, fairness, or reproducibility when methods or results are absent.
- Prefer `partial_with_gaps` or `needs_source_material` over false completeness.

## Supplement-dependent conclusions

- Mark claims as `supplement_dependent` when the main paper points to supplemental evidence not supplied.
- Do not treat "see supplement" as visible support.
- State which conclusions remain weaker without the supplement.
- If citation metadata is incomplete because the full references live only in supplement or appendix material, keep the unresolved entry in `citation_notes.md` and leave the BibTeX item incomplete rather than inventing fields.

## No repository found

- Separate "no repository referenced" from "repository not yet located".
- Still complete paper-side analysis.
- Mark `model/` outputs as paper-inferred only.

## Repository exists but is incomplete

- Record what is visible and what is missing.
- Note absent training scripts, hidden preprocessing, missing checkpoints, or undocumented evaluation logic.
- Avoid saying the method is reproducible if central pieces are absent.

## Equations present but code missing

- Build a conservative conceptual flow from the paper.
- Keep tensor or interface details at high level unless the paper explicitly states them.
- Use `math_to_code_map.md` to note missing implementation anchors rather than faking them.

## Code present but does not run

- Separate static code inspection from execution verification.
- Do not claim failure cause unless the error is directly observed.
- Mark `reproduction_confirmed` as false or unverified.

## Tensor shapes cannot be inferred cleanly

- Describe the flow at the module or operation level.
- State exactly where the uncertainty begins.
- Prefer "shape path unclear after fusion block" over made-up dimensions.

## Paper overclaims beyond visible evidence

- Preserve the claim as the authors state it.
- Mark support as `partial`, `indirect`, or `unsupported_from_supplied_material`.
- Explain why the evidence level is weaker than the prose suggests.
- Avoid inflammatory language; keep the critique source-grounded.

## User asks for stronger conclusions than the paper supports

- Refuse the stronger unsupported conclusion.
- Offer the strongest defensible wording instead.
- Make the limitation explicit in `summary.md` or `failure_report.md`.

## Mixed paper and code version mismatch

- Report the mismatch explicitly.
- Do not assume the latest repository mirrors the publication version.
- Treat any paper-to-code mapping as tentative unless version alignment is visible.

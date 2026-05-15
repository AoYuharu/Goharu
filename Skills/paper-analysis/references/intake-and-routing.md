# Intake and routing

Use this file before reading deeply or drafting outputs. Its job is to decide what the user wants,
what source material exists, how far the analysis can honestly go, and whether the skill should
proceed with placeholders or stop for missing evidence.

## Task modes

| Mode | Use when | Minimum useful input | Default output |
|---|---|---|---|
| `full-analysis` | User wants the full engineering-style package | Paper PDF or substantial paper text; figures or tables help; repo optional | Full staged package with `raw/`, `analyze/`, `model/`, summary, and failure report |
| `quick-read` | User wants a fast but traceable read, not full decomposition | Abstract plus some results text, figures, or a paper PDF | Short analysis with explicit evidence limits |
| `triage-only` | User asks what to read, what is missing, or whether the paper is worth deeper analysis | Any fragment of the paper or notes | Readiness assessment, source gaps, and next-step plan |
| `claim-audit` | User wants to know whether claims are supported | Claims plus source text, figures, or paper PDF | Claim-evidence table and overclaim risk notes |
| `benchmark-audit` | User cares about baselines, metrics, fairness, and comparison rigor | Results section, tables, figures, or benchmark paper PDF | Benchmark critique and unsupported-comparison flags |
| `methods-audit` | User cares about method logic, implementation clarity, and reproducibility | Methods text or full paper; code optional | Method decomposition and reproducibility risk map |
| `discussion-prep` | User wants journal club or meeting talking points grounded in evidence | Paper, notes, or figures | Discussion guide with claims, strengths, weaknesses, and open questions |

If the mode is unclear, infer the safest useful mode. Prefer `triage-only` or `quick-read` when the
source is sparse and a deeper mode would invite fabrication.

## Readiness states

Use one package-level readiness state and, when helpful, section-level readiness states:

| State | Meaning | Allowed output |
|---|---|---|
| `complete` | Supplied material supports the requested mode and major conclusions are traceable | Full analysis without hidden placeholders |
| `partial_with_gaps` | Useful analysis is possible, but some sections depend on missing paper parts, supplement, or code | Return partial package with explicit gap labels |
| `needs_source_material` | The requested depth is not credible without more source material | Return a gap map, questions, and limited triage only |
| `blocked` | Access, integrity, or source-quality failures prevent credible analysis | Report blocking issue first; do not overproduce unsupported content |

Do not label a package `complete` if major claim support, experiment details, or repository coverage
remain materially missing.

## Minimum inputs by mode

### `full-analysis`

Usually requires:

- full paper PDF, article text, or substantial sections
- at least abstract plus methods/results coverage
- figures, tables, or legends when visual evidence is central
- repository or code only if the user expects code-aware or `model/` analysis

Routing rule:

- if code is available, `full-analysis` should include the enhanced `model/` workflow
- if code is absent, still proceed, but mark `model/` outputs as paper-inferred rather than repository-verified
- if code is partial, preserve the `model/` artifact structure and mark unresolved dependencies instead of fabricating runnability

### `claim-audit`

Requires:

- a paper or excerpt containing the claims
- some evidence source, such as results text, figures, tables, or appendix text

If only claims are provided without supporting material, return `needs_source_material`.

### `benchmark-audit`

Requires:

- benchmark tables, reported metrics, or results sections
- ideally baseline names, task definitions, and evaluation settings

If baseline conditions are missing, flag fairness limits instead of inferring them.

### `methods-audit`

Requires:

- methods section or architecture description
- equations, diagrams, or code if available

Routing rule:

- if code is available, activate the deep module-analysis protocol
- if code is absent, still analyze the method from the paper, but separate conceptual logic from implementation confidence
- if code is partial, keep the `model/core_modules/` structure while marking blocked extraction boundaries and uncertain shapes

### `discussion-prep`

Can proceed with:

- abstract plus selected figures
- reading notes
- partial excerpts

But the discussion guide must visibly separate strong support from tentative interpretation.

## Deep module-analysis activation rules

Activate the detailed `model/` protocol whenever the user asks for:

- code understanding
- repo-grounded model analysis
- module extraction
- tensor or data-flow tracing
- implementation fidelity
- math-to-code mapping

Default routing consequences:

- `full-analysis` + code available -> include the full enhanced `model/` workflow
- `methods-audit` + code available -> include the full enhanced `model/` workflow
- code unavailable -> preserve the structure but downgrade to conceptual paper-inferred module analysis
- code partial -> preserve per-module folders and mark blocked or unresolved pieces explicitly

## Proceed versus stop rules

Proceed with placeholders or partial sections when:

- the missing material affects depth but not basic orientation
- a meaningful claim-evidence or experiment critique can still be produced
- the user asked for a quick read, triage, or discussion prep
- repository analysis is requested but the paper itself is analyzable without it
- entrypoint tracing is partial but a credible builder or top-level `forward()` trace still exists

Stop and return `needs_source_material` or `blocked` when:

- the user asks for strong conclusions that the supplied material cannot support
- the source is too fragmented to identify the paper's main claim or evaluation setting
- the PDF is scanned or corrupted and no readable content can be recovered
- code or repository access is central to the request but unavailable
- the user asks for specific metrics, tensor paths, module behavior, or implementation details not present in supplied material

## Clarifying question policy

Usually proceed first and ask later. Ask concise questions only when:

- the user explicitly wants a complete package and required source material is missing
- multiple papers, repository branches, or paper versions are mixed together
- the requested analysis depends on knowing which task mode matters most
- the repository path or URL is ambiguous
- the skill would otherwise have to invent experiments, metrics, citations, code paths, or module boundaries

Example:

```text
I can start a partial analysis now, but to make the benchmark audit credible I still need the main
results table or the exact baseline list.
```

## Routing shortcuts

- Only abstract plus a few claims -> `quick-read` or `triage-only`.
- Full paper PDF with explicit request for rigorous critique -> `full-analysis`.
- User asks "are these claims supported?" -> `claim-audit`.
- User asks about fairness, SOTA, or missing baselines -> `benchmark-audit`.
- User asks how the model really works, whether the implementation matches the math, or which modules are truly central -> `methods-audit` plus enhanced `model/` workflow.
- User asks for journal club discussion points -> `discussion-prep`.

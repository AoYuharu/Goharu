# `paper-analysis` skill

A documentation-first skill for turning papers, paper excerpts, and paper-plus-repository bundles
into traceable engineering analysis packages.

This skill is not a plain summary writer. It keeps `raw/`, `analyze/`, and `model/` reasoning
visible, separates observed evidence from inference, and exposes failure states instead of hiding
them.

## What it does

- identifies the task mode: full analysis, quick read, triage-only, claim audit, benchmark audit, methods audit, or discussion prep
- extracts paper structure, figures, tables, claims, and supporting evidence without inventing missing details
- builds claim-to-evidence and risk mappings instead of only writing a narrative summary
- critiques experiments, baselines, controls, metrics, ablations, and implementation transparency
- inspects supplied repositories or code folders when available and separates observed code facts from inferred behavior
- traces model entrypoints, top-level `forward()` paths, training/eval routes, and code-grounded core neural modules when code exists
- decomposes central modules into source-grounded engineering artifacts with snippets, fake-input forward plans, shape traces, math-to-code mappings, and engineering analysis
- reports failure states such as scanned PDFs, partial excerpts, missing repository coverage, or unverifiable claims instead of hiding them

## When to use

- turning a paper PDF into a traceable engineering analysis package
- auditing whether a paper's main claims are actually supported by supplied evidence
- preparing benchmark, methods, or systems-focused reading notes for engineering discussion
- mapping equations, modules, training logic, and tensor/data flow to repository structure when code is available
- understanding which neural modules are actually central in a real implementation rather than only in the paper prose
- identifying missing baselines, weak comparisons, supplement-dependent claims, or implementation gaps

## What it returns

Unless the user asks for another format, the skill returns:

1. intake and readiness state
2. structured analysis package outline or workspace tree
3. claim-evidence and experiment critique sections
4. code/repository and `model/` analysis when code exists
5. explicit uncertainty and failure reporting

## Core rules

- Separate `SOURCE_FACT`, `AUTHOR_CLAIM`, and `ASSISTANT_INFERENCE`.
- Do not invent metrics, datasets, hyperparameters, citations, repositories, tensor shapes, code behavior, or results.
- Prefer partial but honest analysis over polished unsupported conclusions.
- Preserve the staged workflow when the request is execution-style: `raw/`, `analyze/`, `model/`, `summary.md`, and `failure_report.md`.
- When code is available, ground `model/` in actual entrypoint, builder, and forward-path tracing rather than folder-level guesses.
- Do not claim runnable extraction, shape verification, or code-paper fidelity unless directly supported by inspected source.

## File structure

```text
paper-analysis/
├── README.md
├── SKILL.md
├── references/
│   ├── intake-and-routing.md
│   ├── source-hierarchy.md
│   ├── workflow-map.md
│   ├── analysis-structure.md
│   ├── module-extraction-protocol.md
│   ├── claim-evidence-taxonomy.md
│   ├── engineering-paper-types.md
│   ├── difficult-cases.md
│   ├── qa-checklist.md
│   ├── output-templates.md
│   └── chinese-alignment.md
├── examples/
│   ├── methods-paper-basic.md
│   ├── methods-paper-code-grounded.md
│   └── benchmark-paper.md
└── tests/
    ├── rubric.md
    ├── evaluation-summary.md
    ├── methods-paper-complete.md
    ├── core-modules-code-grounded.md
    ├── tensor-flow-fallback-shape-table.md
    └── insufficient-evidence-input.md
```

## Status

Beta. The behavior is defined by Markdown operating rules, examples, and test fixtures. Keep the
skill below Stable until it has been exercised on real paper-analysis requests spanning PDF-only,
paper-plus-code, and incomplete-source cases.

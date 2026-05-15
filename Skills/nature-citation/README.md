# `nature-citation` skill

A citation-search skill for turning manuscript text or standalone claims into strict Nature / CNS-family reference exports with segment-level mapping and reference-manager-ready downloads.

This skill is bilingual-aware. It accepts Chinese manuscript text and citation requests such as "分段引用", "Nature系列引用", "CNS及子刊", "补引用", "支撑文献", or "导出 Zotero", then searches with English scientific concepts while returning Chinese review notes by default.

## What it does

- splits manuscript text into citable segments with stable IDs such as `S001`, `S002`, and `S003`
- converts each segment into search queries for Crossref-led discovery
- filters results to Nature Portfolio, the AAAS Science family, Cell Press, or flagship-only scope
- maps each segment to candidate citations and suggested in-text insertion markers
- exports one reference-manager file in `ENW`, `RIS`, or Zotero `RDF`
- optionally builds JSON, TSV, Markdown, and HTML review artifacts for manual screening
- supports DOI-only export when the user already knows which records should be included

## Source hierarchy

- Crossref structured metadata and DOI records
- PubMed / NCBI E-utilities for biomedical cross-checking when relevant
- Official publisher pages from Nature Portfolio, AAAS Science, and Cell Press
- Secondary scholarly indexes only as discovery aids, never as the sole support basis

## File structure

```text
nature-citation/
├── SKILL.md
├── README.md
├── references/
│   ├── journal-scope.md
│   ├── ris-endnote.md
│   └── search-strategy.md
└── scripts/
    └── nature_citation.py
```

## When to use

- adding citations to a paragraph, abstract, introduction, results, or discussion section
- turning long text into segment-by-segment citation candidates
- restricting references to `Nature系列`, `CNS`, `CNS及其子刊`, or `只看正刊`
- exporting references for EndNote, Zotero, or other citation managers
- screening whether a sentence has direct support, partial support, or only background support
- producing an HTML review page where the user filters by year, selects citations, and downloads only the records they want

## Design intent

The skill should prioritize defensibility over volume. It is designed to help the user find likely in-scope papers, not to pretend that metadata alone proves a claim. Every exported record should preserve real metadata, avoid fabricated fields, and make the evidence-review burden explicit.

## Reference map

- `search-strategy.md`: claim decomposition, support grades, and common retrieval failure modes
- `journal-scope.md`: Nature / Science / Cell family boundaries and flagship-only interpretation
- `ris-endnote.md`: ENW, RIS, and Zotero RDF export guidance
- `scripts/nature_citation.py`: local CLI for segmentation, Crossref retrieval, export, and HTML review generation

## Notes

- Default output is a single reference-manager file; additional artifacts are opt-in.
- `metadata-only candidate` means the abstract or full text still needs human review before citation.
- The HTML review page can export selected references as `ENW`, `RIS`, or Zotero `RDF`.

# PDF Reading
Comprehensive PDF reading and analysis instructions.
---
When reading PDF files:

1. **Tool Selection**: Use the `Read` tool with the PDF file path to read pages. Large PDFs (>10 pages) require specifying page ranges.

2. **Page-by-Page Reading**: For long papers, read in chunks (e.g., pages 1-5, 6-10) to stay within limits. Start with the abstract/introduction (usually pages 1-2) to understand the paper's focus.

3. **Figure Extraction**: Note figure references in the text. Use `Read` with specific page numbers to locate figures and their captions.

4. **Structure Navigation**: Read the first 2-3 pages first to get the abstract, introduction, and paper structure. Then request specific sections as needed:
   - Methodology: usually middle pages
   - Results/Experiments: after methodology
   - Conclusion: last 2-3 pages

5. **Analysis Output**: After reading, provide:
   - Core contribution summary
   - Key methodology overview
   - Main results and claims
   - Limitations noted by authors
   - Your own assessment

6. **Error Handling**: If `Read` fails on a PDF, check the file extension is `.pdf` and the path is correct. Try reading with `pages: "1-5"` explicitly.

export function nowHHMMSS(): string {
  return new Date().toTimeString().slice(0, 8);
}

/** Port of Agent.TokenEstimator.format: "523" or "12.3k" */
export function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

/** Compact form for large token counts: "875.0k" / "5.7M" (status bar breakdowns) */
export function formatTokensCompact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function separator(): string {
  const cols = process.stdout.columns || 80;
  return "─".repeat(Math.min(80, cols));
}

/**
 * Highlight a pre-styled line robustly. Naive chalk.bgBlue(line) fails on
 * renderMarkdown output because every line starts with \x1b[0m (full reset),
 * which instantly kills the background — so re-open the highlight after
 * every inner reset.
 */
export function highlightLine(
  line: string,
  open = "\x1b[44m", // blue background (selection)
  close = "\x1b[49m",
): string {
  return open + line.replaceAll("\x1b[0m", "\x1b[0m" + open) + close;
}

/** Hover variant: inverse video, same reset-proof wrapping. */
export function hoverLine(line: string): string {
  return highlightLine(line, "\x1b[7m", "\x1b[27m");
}

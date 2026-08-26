export function nowHHMMSS(): string {
  return new Date().toTimeString().slice(0, 8);
}

/** Port of Agent.TokenEstimator.format: "523" or "12.3k" */
export function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function separator(): string {
  const cols = process.stdout.columns || 80;
  return "─".repeat(Math.min(80, cols));
}

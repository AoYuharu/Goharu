/**
 * Lightweight LaTeX → Unicode pretty-printer for terminal display.
 * Covers common constructs (greek, operators, super/subscripts, \frac,
 * \sqrt); unknown commands degrade gracefully to their name.
 */

const SYMBOLS: Record<string, string> = {
  // greek lowercase
  "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ",
  "\\epsilon": "ε", "\\varepsilon": "ε", "\\zeta": "ζ", "\\eta": "η",
  "\\theta": "θ", "\\vartheta": "ϑ", "\\iota": "ι", "\\kappa": "κ",
  "\\lambda": "λ", "\\mu": "μ", "\\nu": "ν", "\\xi": "ξ",
  "\\pi": "π", "\\varpi": "ϖ", "\\rho": "ρ", "\\varrho": "ϱ",
  "\\sigma": "σ", "\\varsigma": "ς", "\\tau": "τ", "\\upsilon": "υ",
  "\\phi": "φ", "\\varphi": "φ", "\\chi": "χ", "\\psi": "ψ", "\\omega": "ω",
  // greek uppercase
  "\\Gamma": "Γ", "\\Delta": "Δ", "\\Theta": "Θ", "\\Lambda": "Λ",
  "\\Xi": "Ξ", "\\Pi": "Π", "\\Sigma": "Σ", "\\Upsilon": "Υ",
  "\\Phi": "Φ", "\\Psi": "Ψ", "\\Omega": "Ω",
  // operators & relations
  "\\sum": "∑", "\\prod": "∏", "\\int": "∫", "\\iint": "∬", "\\oint": "∮",
  "\\infty": "∞", "\\partial": "∂", "\\nabla": "∇", "\\propto": "∝",
  "\\leq": "≤", "\\le": "≤", "\\geq": "≥", "\\ge": "≥", "\\neq": "≠",
  "\\ne": "≠", "\\approx": "≈", "\\equiv": "≡", "\\sim": "∼",
  "\\pm": "±", "\\mp": "∓", "\\times": "×", "\\cdot": "·", "\\div": "÷",
  "\\circ": "∘", "\\bullet": "•", "\\ast": "∗", "\\star": "⋆",
  "\\in": "∈", "\\notin": "∉", "\\ni": "∋", "\\subset": "⊂",
  "\\supset": "⊃", "\\subseteq": "⊆", "\\supseteq": "⊇",
  "\\cup": "∪", "\\cap": "∩", "\\setminus": "∖", "\\emptyset": "∅",
  "\\forall": "∀", "\\exists": "∃", "\\neg": "¬", "\\lnot": "¬",
  "\\land": "∧", "\\wedge": "∧", "\\lor": "∨", "\\vee": "∨",
  "\\rightarrow": "→", "\\to": "→", "\\leftarrow": "←", "\\gets": "←",
  "\\Rightarrow": "⇒", "\\Leftarrow": "⇐", "\\leftrightarrow": "↔",
  "\\Leftrightarrow": "⇔", "\\mapsto": "↦", "\\uparrow": "↑", "\\downarrow": "↓",
  "\\ldots": "…", "\\cdots": "⋯", "\\vdots": "⋮", "\\ddots": "⋱",
  "\\angle": "∠", "\\perp": "⊥", "\\parallel": "∥", "\\degree": "°",
  "\\hbar": "ℏ", "\\ell": "ℓ", "\\Re": "ℜ", "\\Im": "ℑ", "\\aleph": "ℵ",
  "\\max": "max", "\\min": "min", "\\arg": "arg", "\\sup": "sup",
  "\\inf": "inf", "\\lim": "lim", "\\log": "log", "\\ln": "ln",
  "\\exp": "exp", "\\sin": "sin", "\\cos": "cos", "\\tan": "tan",
  "\\det": "det", "\\dim": "dim", "\\ker": "ker", "\\mod": "mod",
};

const SUPERSCRIPT: Record<string, string> = {
  "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶",
  "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽",
  ")": "⁾", "n": "ⁿ", "i": "ⁱ", "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ",
  "e": "ᵉ", "f": "ᶠ", "g": "ᵍ", "h": "ʰ", "j": "ʲ", "k": "ᵏ", "l": "ˡ",
  "m": "ᵐ", "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ",
  "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ", "T": "ᵀ", "*": "∗",
};

const SUBSCRIPT: Record<string, string> = {
  "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆",
  "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋", "=": "₌", "(": "₍",
  ")": "₎", "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
  "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ", "s": "ₛ",
  "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
};

// Longest-first so \subseteq wins over \subset, \iint over \int, etc.
const SYMBOL_KEYS = Object.keys(SYMBOLS).sort((a, b) => b.length - a.length);

function toSuper(s: string): string {
  const chars = [...s];
  return chars.every((c) => c in SUPERSCRIPT)
    ? chars.map((c) => SUPERSCRIPT[c]).join("")
    : "^" + s; // unmapped chars (e.g. ∞): keep ^ notation
}
function toSub(s: string): string {
  const chars = [...s];
  return chars.every((c) => c in SUBSCRIPT)
    ? chars.map((c) => SUBSCRIPT[c]).join("")
    : "_" + s;
}

/** Convert a LaTeX math fragment to a readable Unicode one-liner. */
export function convertLatex(src: string): string {
  let s = src;
  s = s.replace(/\\(?:left|right|middle|big|Big|bigg|Bigg)\s*([()[\]|])/g, "$1");
  s = s.replace(/\\(?:left|right|middle|big|Big|bigg|Bigg)\s*[a-zA-Z]*/g, "");
  s = s.replace(/\\[dt]?frac\{([^{}]+)\}\{([^{}]+)\}/g, "($1)⁄($2)");
  s = s.replace(/\\sqrt\[([^\]]+)\]\{([^{}]+)\}/g, (_m, n, x) => `${toSuper(n)}√(${x})`);
  s = s.replace(/\\sqrt\{([^{}]+)\}/g, "√($1)");
  s = s.replace(/\\(?:text|mathrm|mathbf|mathit|operatorname|boldsymbol?)\{([^{}]*)\}/g, "$1");
  s = s.replace(/\\(?:hat|bar|vec|tilde|dot|ddot|overline)\{([^{}]*)\}/g, "$1");
  for (const key of SYMBOL_KEYS) {
    if (s.includes(key)) s = s.replaceAll(key, SYMBOLS[key]);
  }
  s = s.replace(/\^\{([^{}]+)\}/g, (_m, p) => toSuper(p));
  s = s.replace(/\^([A-Za-z0-9+\-*/])/g, (_m, p) => toSuper(p));
  s = s.replace(/_\{([^{}]+)\}/g, (_m, p) => toSub(p));
  s = s.replace(/_([A-Za-z0-9])/g, (_m, p) => toSub(p));
  s = s.replace(/\\[,;:!~]/g, " ").replace(/\\quad?/g, "  ");
  s = s.replace(/\\[a-zA-Z]+/g, (m) => m.slice(1)); // unknown \cmd → cmd
  s = s.replace(/[{}]/g, "");
  return s.replace(/\s+/g, " ").trim();
}

export interface MathExtract {
  text: string;          // input with math replaced by @@MATHn@@ tokens
  blocks: string[];      // converted formulas (index-aligned with tokens)
  isBlock: boolean[];
}

/**
 * Extract $$...$$, \[...\], \(...\) and $...$ math so the markdown parser
 * never sees LaTeX internals (underscores would become <em> etc).
 */
export function extractMath(input: string): MathExtract {
  const blocks: string[] = [];
  const isBlock: boolean[] = [];
  const text = input.replace(
    /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$([^$\n]+?)\$/g,
    (_m, dbl, brack, paren, inline) => {
      const raw = dbl ?? brack ?? paren ?? inline;
      const block = dbl !== undefined || brack !== undefined;
      blocks.push(convertLatex(String(raw)));
      isBlock.push(block);
      return `@@MATH${blocks.length - 1}@@`;
    },
  );
  return { text, blocks, isBlock };
}

/** Put converted formulas back after markdown rendering. */
export function restoreMath(rendered: string, blocks: string[], style: (s: string) => string): string {
  return rendered.replace(/@@MATH(\d+)@@/g, (_m, i) => style(blocks[Number(i)] ?? ""));
}

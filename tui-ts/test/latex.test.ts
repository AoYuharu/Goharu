import { convertLatex } from "../src/latex.js";
import { renderMarkdown } from "../src/markdown.js";

let failed = 0;
function check(name: string, cond: boolean, extra = "") {
  if (!cond) failed++;
  console.log(`${cond ? "PASS" : "FAIL"} ${name}${extra ? " — " + extra : ""}`);
}

// LaTeX → Unicode conversion
check("greek + superscript", convertLatex("\\alpha^2 + \\beta_1") === "α² + β₁");
check("fraction", convertLatex("\\frac{n+1}{2}") === "(n+1)⁄(2)");
check("sqrt", convertLatex("\\sqrt{x^2+1}") === "√(x²+1)");
check("sum with bounds", convertLatex("\\sum_{i=1}^{n} i") === "∑ᵢ₌₁ⁿ i");
check("relations", convertLatex("x \\leq y \\neq z \\in A") === "x ≤ y ≠ z ∈ A");
check("operators", convertLatex("\\int_0^{\\infty} e^{-x} dx") === "∫₀^∞ e⁻ˣ dx");
check("unknown cmd degrades", convertLatex("\\foo{bar}") === "foobar");
check("left/right stripped", convertLatex("\\left( \\frac{a}{b} \\right)") === "( (a)⁄(b) )");

// End-to-end: markdown + math, no raw asterisks / dollars left
const md = renderMarkdown("期望是 $E[x] = \\sum_{i=1}^{n} p_i x_i$，**不是**斜体。\n\n$$\\frac{a}{b} \\times \\pi$$");
check("no leftover **", !md.includes("**"));
check("no leftover $", !md.includes("$"));
check("no leftover @@MATH", !md.includes("@@MATH"));
check("formula converted", md.includes("∑ᵢ₌₁ⁿ") && md.includes("(a)⁄(b) × π"));

console.log("--- sample ---");
console.log(md);
process.exit(failed ? 1 : 0);

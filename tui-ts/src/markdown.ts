import { Marked } from "marked";
import { markedTerminal } from "marked-terminal";
import { Chalk } from "chalk";
import { extractMath, restoreMath } from "./latex.js";

// marked-terminal 的默认样式用的是全局 chalk 实例，其颜色级别在 import 时检测，
// 在非 TTY / 部分 Ink 环境下为 0 → 输出无任何样式，看起来像"没渲染"。
// 这里强制 256 色实例，保证样式始终生效。
const c = new Chalk({ level: 2 });

const mathStyle = (s: string) => c.cyan(s);

/**
 * Render markdown to an ANSI-styled terminal string.
 * Width is fixed at render time (viewport never re-wraps on resize).
 * LaTeX math ($...$ / $$...$$ / \(...\) / \[...\]) is extracted before
 * parsing and rendered as Unicode formulas in cyan.
 */
export function renderMarkdown(text: string): string {
  try {
    const math = extractMath(text);
    const width = Math.max(40, (process.stdout.columns || 80) - 4);
    const m = new Marked(
      markedTerminal({
        width,
        showSectionPrefix: false, // 标题不带 ## 前缀，只靠颜色/粗体区分
        code: c.yellow,
        blockquote: c.gray.italic,
        html: c.gray,
        heading: c.green.bold,
        firstHeading: c.magenta.underline.bold,
        hr: c.reset,
        listitem: c.reset,
        table: c.reset,
        paragraph: c.reset,
        strong: c.bold,
        em: c.italic,
        codespan: c.yellow,
        del: c.dim.gray.strikethrough,
        link: c.blue,
        href: c.blue.underline,
      }),
    );
    const out = m.parse(math.text, { async: false });
    return restoreMath(out.replace(/\n+$/, ""), math.blocks, mathStyle);
  } catch {
    return text;
  }
}

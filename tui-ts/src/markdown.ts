import { Marked } from "marked";
import { markedTerminal } from "marked-terminal";
import { Chalk } from "chalk";

// marked-terminal 的默认样式用的是全局 chalk 实例，其颜色级别在 import 时检测，
// 在非 TTY / 部分 Ink 环境下为 0 → 输出无任何样式，看起来像"没渲染"。
// 这里强制 256 色实例，保证样式始终生效。
const c = new Chalk({ level: 2 });

/**
 * Render markdown to an ANSI-styled terminal string.
 * Width is fixed at render time (Static scrollback never re-wraps on resize).
 */
export function renderMarkdown(text: string): string {
  try {
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
    const out = m.parse(text, { async: false });
    return out.replace(/\n+$/, "");
  } catch {
    return text;
  }
}

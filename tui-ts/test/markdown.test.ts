import { renderMarkdown } from "../src/markdown.js";

const input = "这是**加粗**和*斜体*，代码 `x=1`：\n\n- 列表一\n- 列表二\n\n## 标题\n\n```py\nprint(1)\n```";
const out = renderMarkdown(input);
console.log("=== RAW JSON ===");
console.log(JSON.stringify(out));
console.log("=== RENDERED ===");
console.log(out);
console.log("=== has leftover **:", out.includes("**"), " has \\033:", out.includes(""));

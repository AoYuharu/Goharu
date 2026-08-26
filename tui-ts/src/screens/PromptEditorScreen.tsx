import React, { useEffect, useState } from "react";
import { Box, Text, useInput } from "ink";
import { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { PROJECT_ROOT } from "../gateway/GatewayClient.js";

function scanPromptFiles(): string[] {
  const root = path.join(PROJECT_ROOT, "prompts");
  const out: string[] = [];
  const walk = (dir: string) => {
    let entries: string[];
    try {
      entries = readdirSync(dir);
    } catch {
      return;
    }
    for (const name of entries) {
      const full = path.join(dir, name);
      try {
        if (statSync(full).isDirectory()) walk(full);
        else if (name.endsWith(".md")) out.push(path.relative(PROJECT_ROOT, full));
      } catch { /* skip unreadable entries */ }
    }
  };
  if (existsSync(root)) walk(root);
  return out.sort();
}

/**
 * Prompt file browser: left list + right preview.
 * In-app editing is not ported from the Textual TextArea; `e` opens the file
 * in the system editor, `r` reloads the preview after external edits.
 */
export function PromptEditorScreen({
  active,
  onClose,
}: {
  active: boolean;
  onClose: () => void;
}) {
  const [files, setFiles] = useState<string[]>([]);
  const [index, setIndex] = useState(0);
  const [preview, setPreview] = useState("");
  const [notice, setNotice] = useState("");

  const loadPreview = (i: number, list = files) => {
    const rel = list[i];
    if (!rel) {
      setPreview("");
      return;
    }
    try {
      setPreview(readFileSync(path.join(PROJECT_ROOT, rel), "utf8"));
    } catch (e) {
      setPreview(`(failed to read: ${e})`);
    }
  };

  useEffect(() => {
    if (!active) return;
    const list = scanPromptFiles();
    setFiles(list);
    setIndex(0);
    loadPreview(0, list);
    setNotice("");
  }, [active]);

  useInput((input, key) => {
    if (key.escape) {
      onClose();
      return;
    }
    if (key.upArrow) {
      setIndex((i) => {
        const next = Math.max(0, i - 1);
        loadPreview(next);
        return next;
      });
      return;
    }
    if (key.downArrow) {
      setIndex((i) => {
        const next = Math.min(files.length - 1, i + 1);
        loadPreview(next);
        return next;
      });
      return;
    }
    if (input === "r") {
      loadPreview(index);
      setNotice("已重新加载");
      return;
    }
    if (input === "e" || key.return) {
      const rel = files[index];
      if (!rel) return;
      const full = path.join(PROJECT_ROOT, rel);
      const editor = process.platform === "win32" ? "notepad" : (process.env.EDITOR ?? "vi");
      spawn(editor, [full], { detached: true, stdio: "ignore" }).unref();
      setNotice(`已在外部编辑器打开: ${rel}（编辑后按 r 刷新预览）`);
    }
  }, { isActive: active });

  if (!active) return null;

  const height = Math.max(8, (process.stdout.rows || 24) - 8);
  const previewLines = preview.split("\n").slice(0, height);

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="magenta">
      <Text bold color="magenta"> 📝 Prompt Files </Text>
      <Box>
        <Box flexDirection="column" width="40%">
          {files.map((f, i) => (
            <Text
              key={f}
              wrap="truncate"
              backgroundColor={i === index ? "magenta" : undefined}
              color={i === index ? "white" : undefined}
            >
              {" "}{f}
            </Text>
          ))}
          {files.length === 0 && <Text dimColor>  (no prompt files found)</Text>}
        </Box>
        <Box flexDirection="column" width="60%" borderLeft borderColor="gray" paddingLeft={1}>
          {previewLines.map((line, i) => (
            <Text key={i} dimColor wrap="truncate">{line || " "}</Text>
          ))}
        </Box>
      </Box>
      {notice && <Text color="yellow">{notice}</Text>}
      <Text dimColor>↑↓ Navigate | Enter/e Edit in external editor | r Reload | Esc Back</Text>
    </Box>
  );
}

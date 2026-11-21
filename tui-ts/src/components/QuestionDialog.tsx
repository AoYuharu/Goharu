import React from "react";
import { Box, Text } from "ink";
import wrapAnsi from "wrap-ansi";
import type { UserQuestionOption } from "../gateway/protocol.js";
import { TextInput } from "./TextInput.js";

export interface UserQuestionItem {
  question: string;
  options: UserQuestionOption[];
  multi_select?: boolean;
}

export interface UserQuestionRequest {
  request_id: string;
  questions: UserQuestionItem[];
}

/** 批量问卷模式下每题的作答：单选=string，多选=string[]，未作答=undefined */
export type QuestionAnswers = Record<number, string | string[]>;

const PANEL_BG = "#1c1e2b";
const ACCENT = "#7c6fd0";

/** Fixed panel height (incl. border) — App relies on this for viewport math. */
export const QUESTION_PANEL_ROWS = 12;
/** Max option rows visible at once; list scrolls around the selection. */
const OPTION_WINDOW = 5;

/** 是否走向导面板（多问或含多选）；否则用单问面板（含自填行） */
export const isBatchQuestion = (req: UserQuestionRequest) =>
  req.questions.length > 1 || req.questions.some((q) => q.multi_select);

/** Index of the always-appended custom-entry row（仅单问模式）. */
export const customIndex = (req: UserQuestionRequest) => (req.questions[0]?.options.length ?? 0);

export function QuestionDialog({
  req,
  selIndex,
  inputMode,
  draft,
  onDraftChange,
  onDraftSubmit,
  answers,
  page,
}: {
  req: UserQuestionRequest;
  selIndex: number;
  inputMode: boolean;
  draft: string;
  onDraftChange: (v: string) => void;
  onDraftSubmit: (v: string) => void;
  answers?: QuestionAnswers; // 向导模式传入
  page?: number; // 向导模式当前题号（0-based）
}) {
  const width = Math.max(30, (process.stdout.columns || 80) - 4);
  const wrap = (s: string) =>
    s.split("\n").flatMap((l) => wrapAnsi(l || " ", width, { hard: true, trim: false }).split("\n"));

  if (isBatchQuestion(req)) {
    return (
      <WizardDialog
        req={req}
        page={Math.max(0, Math.min(page ?? 0, req.questions.length - 1))}
        selIndex={selIndex}
        answers={answers ?? {}}
        width={width}
      />
    );
  }

  // ── 单问模式（保持原交互：选项 + 自填行 + 窗口滚动）──
  const single = req.questions[0];
  const options = single?.options ?? [];
  const questionLines = wrap(single?.question ?? "").slice(0, 2);

  const total = options.length + 1;
  const start = Math.max(0, Math.min(selIndex - Math.floor(OPTION_WINDOW / 2), total - OPTION_WINDOW));
  const visible = Array.from({ length: Math.min(OPTION_WINDOW, total) }, (_, i) => start + i);

  const itemRow = (idx: number) => {
    const active = idx === selIndex;
    const isCustom = idx === options.length;
    const label = isCustom ? "✎ 自己填写…" : options[idx].label;
    const desc = !isCustom && options[idx].description ? ` — ${options[idx].description}` : "";
    const text = `${active ? "› " : "  "}${label}${desc}`;
    return (
      <Text key={idx} backgroundColor={PANEL_BG} wrap="truncate"
        color={active ? "#c9c4f0" : "gray"} bold={active}>
        {text}
      </Text>
    );
  };

  return (
    <Box
      flexDirection="column"
      height={QUESTION_PANEL_ROWS}
      borderStyle="round"
      borderColor={ACCENT}
      backgroundColor={PANEL_BG}
      paddingX={1}
    >
      <Text backgroundColor={PANEL_BG}>
        <Text bold color={ACCENT}>❓ 需要你的选择</Text>
        <Text dimColor>  助手正在等待回答</Text>
      </Text>
      {questionLines.map((l, i) => (
        <Text key={i} backgroundColor={PANEL_BG} color="white">{l}</Text>
      ))}
      {Array.from({ length: 2 - questionLines.length }, (_, i) => (
        <Text key={`pad${i}`} backgroundColor={PANEL_BG}> </Text>
      ))}
      {visible.map(itemRow)}
      {Array.from({ length: OPTION_WINDOW - visible.length }, (_, i) => (
        <Text key={`optpad${i}`} backgroundColor={PANEL_BG}> </Text>
      ))}
      {inputMode ? (
        <Box backgroundColor={PANEL_BG}>
          <Text backgroundColor={PANEL_BG} color={ACCENT}>{"> "}</Text>
          <TextInput value={draft} onChange={onDraftChange} onSubmit={onDraftSubmit}
            placeholder="输入你的回答…" focus={inputMode} />
        </Box>
      ) : (
        <Text backgroundColor={PANEL_BG}> </Text>
      )}
      <Text dimColor backgroundColor={PANEL_BG}>
        {inputMode ? "Enter 提交 | Esc 返回选项" : "↑↓ 选择 | Enter 确认 | Esc 取消"}
      </Text>
    </Box>
  );
}

/** 向导面板：一页一题。单选 Enter/空格=选中并下一题（末题提交）；
 *  多选 空格=勾选（停留）、Enter=下一题（末题提交）；←→ 随时切题。 */
function WizardDialog({
  req,
  page,
  selIndex,
  answers,
  width,
}: {
  req: UserQuestionRequest;
  page: number;
  selIndex: number;
  answers: QuestionAnswers;
  width: number;
}) {
  const q = req.questions[page];
  const total = req.questions.length;
  const isLast = page === total - 1;
  const ans = answers[page];

  const questionLine =
    wrapAnsi(q.question + (q.multi_select ? "（可多选）" : ""), width - 4, {
      hard: true, trim: false,
    }).split("\n")[0] ?? "";

  const optionRow = (oi: number) => {
    const opt = q.options[oi];
    const active = oi === selIndex;
    let mark: string;
    if (q.multi_select) {
      mark = Array.isArray(ans) && ans.includes(opt.label) ? "[✓]" : "[ ]";
    } else {
      mark = ans === opt.label ? "(●)" : "( )";
    }
    const text = `${active ? "›" : " "}${mark} ${opt.label}${opt.description ? ` — ${opt.description}` : ""}`;
    return (
      <Text key={oi} backgroundColor={PANEL_BG} wrap="truncate"
        color={active ? "#c9c4f0" : "gray"} bold={active}>
        {text}
      </Text>
    );
  };

  const hint = q.multi_select
    ? `空格 勾选 | Enter ${isLast ? "提交全部" : "下一题"} | ←→ 切题 | Esc 取消`
    : `Enter/空格 选中并${isLast ? "提交全部" : "下一题"} | ←→ 切题 | Esc 取消`;

  return (
    <Box
      flexDirection="column"
      height={QUESTION_PANEL_ROWS}
      borderStyle="round"
      borderColor={ACCENT}
      backgroundColor={PANEL_BG}
      paddingX={1}
    >
      <Text backgroundColor={PANEL_BG}>
        <Text bold color={ACCENT}>❓ 需要你的回答</Text>
        <Text dimColor>
          {"  "}第 {page + 1}/{total} 题{isLast ? " · 答完提交" : ""}
        </Text>
      </Text>
      <Text backgroundColor={PANEL_BG} color="white" bold>{questionLine}</Text>
      {q.options.map((_, oi) => optionRow(oi))}
      {Array.from({ length: Math.max(0, 6 - q.options.length) }, (_, i) => (
        <Text key={`pad${i}`} backgroundColor={PANEL_BG}> </Text>
      ))}
      <Text dimColor backgroundColor={PANEL_BG}>{hint}</Text>
    </Box>
  );
}

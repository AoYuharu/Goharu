#!/usr/bin/env python3
"""实时查阅 L1 记忆原子：按 atom_type 分组统计与查看内容。

只读连接 SQLite，不加载 LLM / embedding / jieba，不干扰运行中的 Goharu。

用法：
    python scripts/inspect_l1_atoms.py                 # 总览：各类型数量 + 最近样例
    python scripts/inspect_l1_atoms.py correction      # 查看某类型全部原子
    python scripts/inspect_l1_atoms.py correction -n 20
    python scripts/inspect_l1_atoms.py --search 深色主题
    python scripts/inspect_l1_atoms.py --db path/to/memory.db
    python scripts/inspect_l1_atoms.py --all-status     # 含 inactive 原子
"""
import argparse
import sqlite3
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure:
        _reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "runtime_memory" / "memory.db"

# 各类型的中文说明（与 prompts/summarizer/day_summary.md 一致）
TYPE_DESC = {
    "correction": "用户纠正",
    "constraint": "项目约束",
    "reference": "外部引用",
    "preference": "用户偏好",
    "fact": "工程事实",
    "task_state": "任务状态(启发式)",
}

DISPLAY_ORDER = ["correction", "constraint", "reference", "preference", "fact", "task_state"]


def connect(db_path):
    path = Path(db_path)
    if not path.exists():
        sys.exit(f"[错误] 数据库不存在: {path}")
    # 只读 URI 模式，避免与运行中的实例争写锁
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_stats(conn, include_inactive):
    status_sql = "" if include_inactive else "WHERE status = 'active'"
    rows = conn.execute(
        f"SELECT atom_type, COUNT(*) AS n FROM l1_memory_atoms {status_sql} "
        "GROUP BY atom_type ORDER BY n DESC"
    ).fetchall()
    return {r["atom_type"]: r["n"] for r in rows}


def fetch_by_type(conn, atom_type, limit, include_inactive):
    sql = "SELECT * FROM l1_memory_atoms WHERE atom_type = ?"
    params = [atom_type]
    if not include_inactive:
        sql += " AND status = 'active'"
    sql += " ORDER BY salience DESC, updated_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def fetch_search(conn, keyword, limit, include_inactive):
    sql = "SELECT * FROM l1_memory_atoms WHERE canonical_text LIKE ?"
    params = [f"%{keyword}%"]
    if not include_inactive:
        sql += " AND status = 'active'"
    sql += " ORDER BY salience DESC, updated_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def format_atom(row, show_meta=True):
    line = f"[{row['atom_type']}] {row['canonical_text']}"
    if row["subject"]:
        line += f"  (subject={row['subject']}"
        if row["slot"]:
            line += f", slot={row['slot']}"
        line += ")"
    if show_meta:
        line += f"  salience={row['salience']:.2f}  {row['updated_at'][:16]}"
        if row["status"] != "active":
            line += f"  [{row['status']}]"
    return line


def show_overview(conn, stats, include_inactive):
    total = sum(stats.values())
    print(f"L1 记忆原子总览（{'含 inactive' if include_inactive else '仅 active'}）")
    print(f"总计: {total} 条\n")
    for t in DISPLAY_ORDER:
        if t not in stats:
            continue
        n = stats[t]
        desc = TYPE_DESC.get(t, "?")
        print(f"── {t} ({desc}) × {n}")
        for row in fetch_by_type(conn, t, 3, include_inactive):
            print(f"   {format_atom(row)}")
        print()
    # 未出现在 DISPLAY_ORDER 里的未知类型
    unknown = [t for t in stats if t not in DISPLAY_ORDER]
    if unknown:
        print("── 其他类型")
        for t in unknown:
            print(f"   {t} × {stats[t]}")


def main():
    ap = argparse.ArgumentParser(description="实时查阅 L1 记忆原子")
    ap.add_argument("type", nargs="?", help="原子类型（correction/constraint/reference/preference/fact/task_state）")
    ap.add_argument("-n", "--limit", type=int, default=20, help="显示条数（默认 20）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="数据库路径")
    ap.add_argument("--search", help="按关键词模糊搜索 canonical_text")
    ap.add_argument("--all-status", action="store_true", help="包含 inactive 原子")
    ap.add_argument("--no-meta", action="store_true", help="只显示文本，不显示 salience/时间")
    args = ap.parse_args()

    conn = connect(args.db)
    try:
        if args.search:
            rows = fetch_search(conn, args.search, args.limit, args.all_status)
            print(f"搜索「{args.search}」命中 {len(rows)} 条：\n")
            for r in rows:
                print(format_atom(r, show_meta=not args.no_meta))
            return

        stats = fetch_stats(conn, args.all_status)
        if args.type:
            if args.type not in stats:
                valid = ", ".join(DISPLAY_ORDER)
                sys.exit(f"[错误] 未知类型「{args.type}」，可选：{valid}")
            rows = fetch_by_type(conn, args.type, args.limit, args.all_status)
            desc = TYPE_DESC.get(args.type, "?")
            print(f"类型 {args.type}（{desc}）共 {stats[args.type]} 条，显示 {len(rows)} 条：\n")
            for r in rows:
                print(format_atom(r, show_meta=not args.no_meta))
        else:
            show_overview(conn, stats, args.all_status)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

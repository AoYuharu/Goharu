"""
知识管理工具链 - 用于结构化存储论文分析结果

通过工具强制格式化输出，避免LLM生成格式偏离预期。
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime


class KnowledgeTools:
    """知识管理工具集"""

    BASE_DIR = Path("runtime_memory")
    INSIGHTS_DIR = BASE_DIR / "insights"
    MODULES_DIR = BASE_DIR / "modules"
    PAPERS_DIR = BASE_DIR / "papers"
    CONTEXT_DIR = BASE_DIR / "context"

    @staticmethod
    def _ensure_dirs():
        """确保目录结构存在"""
        for dir_path in [KnowledgeTools.INSIGHTS_DIR,
                         KnowledgeTools.MODULES_DIR,
                         KnowledgeTools.PAPERS_DIR,
                         KnowledgeTools.CONTEXT_DIR]:
            dir_path.mkdir(parents=True, exist_ok=True)

    # ==================== Insight 工具 ====================

    @staticmethod
    def add_insight(
        paper_id: str,
        title: str,
        description: str,
        impact: str,
        category: str = "general",
        tags: Optional[List[str]] = None,
        related_papers: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        添加研究洞察/创新点

        Args:
            paper_id: 论文ID
            title: 洞察标题（如"A有助于XXX指标升高"）
            description: 详细描述
            impact: 影响和意义
            category: 分类（method/architecture/training/evaluation/general）
            tags: 标签列表
            related_papers: 相关论文ID列表

        Returns:
            操作结果
        """
        KnowledgeTools._ensure_dirs()

        insight_file = KnowledgeTools.INSIGHTS_DIR / f"{paper_id}.json"

        # 读取现有数据
        if insight_file.exists():
            with open(insight_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {
                "paper_id": paper_id,
                "insights": [],
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }

        # 添加新洞察
        insight = {
            "id": f"insight_{len(data['insights']) + 1}",
            "title": title,
            "description": description,
            "impact": impact,
            "category": category,
            "tags": tags or [],
            "related_papers": related_papers or [],
            "created_at": datetime.now().isoformat()
        }
        data["insights"].append(insight)
        data["updated_at"] = datetime.now().isoformat()

        # 保存JSON
        with open(insight_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 生成可读的Markdown版本
        md_file = KnowledgeTools.INSIGHTS_DIR / f"{paper_id}.md"
        KnowledgeTools._generate_insight_markdown(paper_id, data, md_file)

        # 更新全局索引
        KnowledgeTools._update_insight_index()

        return {
            "status": "success",
            "insight_id": insight["id"],
            "file": str(insight_file),
            "markdown": str(md_file)
        }

    @staticmethod
    def _generate_insight_markdown(paper_id: str, data: Dict, output_file: Path):
        """生成Insight的Markdown版本"""
        lines = [f"# 研究洞察 - {paper_id}\n"]
        lines.append(f"**创建时间**: {data.get('created_at', 'Unknown')}")
        lines.append(f"**更新时间**: {data.get('updated_at', 'Unknown')}\n")

        for insight in data.get("insights", []):
            lines.append(f"## {insight['title']}")
            lines.append(f"**分类**: {insight.get('category', 'general')}")
            if insight.get('tags'):
                lines.append(f"**标签**: {', '.join(insight['tags'])}")
            lines.append(f"\n### 描述\n{insight['description']}")
            lines.append(f"\n### 影响\n{insight['impact']}")
            if insight.get('related_papers'):
                lines.append(f"\n**相关论文**: {', '.join(insight['related_papers'])}")
            lines.append("\n---\n")

        output_file.write_text("\n".join(lines), encoding='utf-8')

    @staticmethod
    def read_insight(paper_id: str) -> Dict[str, Any]:
        """
        读取某篇论文的洞察

        Args:
            paper_id: 论文ID

        Returns:
            洞察数据
        """
        insight_file = KnowledgeTools.INSIGHTS_DIR / f"{paper_id}.json"

        if not insight_file.exists():
            return {
                "status": "not_found",
                "paper_id": paper_id,
                "message": f"No insights found for {paper_id}"
            }

        with open(insight_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return {
            "status": "success",
            "data": data
        }

    @staticmethod
    def list_insights(
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        列出所有洞察（可筛选）

        Args:
            category: 按分类筛选
            tags: 按标签筛选
            limit: 返回数量限制

        Returns:
            洞察列表
        """
        KnowledgeTools._ensure_dirs()

        all_insights = []

        for insight_file in KnowledgeTools.INSIGHTS_DIR.glob("*.json"):
            with open(insight_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for insight in data.get("insights", []):
                # 筛选
                if category and insight.get("category") != category:
                    continue
                if tags and not any(tag in insight.get("tags", []) for tag in tags):
                    continue

                all_insights.append({
                    "paper_id": data["paper_id"],
                    "insight_id": insight["id"],
                    "title": insight["title"],
                    "category": insight.get("category"),
                    "tags": insight.get("tags", []),
                    "created_at": insight.get("created_at")
                })

        # 按创建时间倒序
        all_insights.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        return {
            "status": "success",
            "total": len(all_insights),
            "insights": all_insights[:limit]
        }

    @staticmethod
    def _update_insight_index():
        """更新全局洞察索引"""
        index_file = KnowledgeTools.INSIGHTS_DIR / "INSIGHT_INDEX.md"

        all_insights = []
        for insight_file in KnowledgeTools.INSIGHTS_DIR.glob("*.json"):
            with open(insight_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for insight in data.get("insights", []):
                all_insights.append({
                    "paper_id": data["paper_id"],
                    "title": insight["title"],
                    "category": insight.get("category", "general"),
                    "created_at": insight.get("created_at", "")
                })

        # 按时间倒序
        all_insights.sort(key=lambda x: x["created_at"], reverse=True)

        lines = ["# 思路索引\n", "## 最新洞察\n"]
        for insight in all_insights[:20]:  # 只显示最新20条
            lines.append(f"- **[{insight['category']}]** {insight['title']} (来源: {insight['paper_id']})")

        index_file.write_text("\n".join(lines), encoding='utf-8')

    # ==================== Module 工具 ====================

    @staticmethod
    def add_module(
        paper_id: str,
        module_name: str,
        category: str,
        principle: str,
        description: str,
        formula: Optional[str] = None,
        complexity: Optional[str] = None,
        code_path: Optional[str] = None,
        github_url: Optional[str] = None,
        use_cases: Optional[List[str]] = None,
        dependencies: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        添加技术模块

        Args:
            paper_id: 论文ID
            module_name: 模块名称
            category: 分类（Core_Mechanisms/Efficiency/Architecture）
            principle: 核心原理（一句话）
            description: 详细描述
            formula: 关键公式
            complexity: 时间复杂度
            code_path: 代码路径（相对于module目录）
            github_url: GitHub仓库链接
            use_cases: 应用场景列表
            dependencies: 依赖的其他模块

        Returns:
            操作结果
        """
        KnowledgeTools._ensure_dirs()

        # 创建模块目录
        module_dir = KnowledgeTools.MODULES_DIR / paper_id / module_name
        module_dir.mkdir(parents=True, exist_ok=True)

        # 创建MODULE.json
        module_data = {
            "paper_id": paper_id,
            "module_name": module_name,
            "category": category,
            "principle": principle,
            "description": description,
            "formula": formula,
            "complexity": complexity,
            "code_path": code_path,
            "github_url": github_url,
            "use_cases": use_cases or [],
            "dependencies": dependencies or [],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }

        module_file = module_dir / "MODULE.json"
        with open(module_file, 'w', encoding='utf-8') as f:
            json.dump(module_data, f, ensure_ascii=False, indent=2)

        # 生成README.md
        readme_file = module_dir / "README.md"
        KnowledgeTools._generate_module_readme(module_data, readme_file)

        # 更新全局索引
        KnowledgeTools._update_module_index()

        return {
            "status": "success",
            "module_path": str(module_dir),
            "module_file": str(module_file),
            "readme": str(readme_file)
        }

    @staticmethod
    def _generate_module_readme(module_data: Dict, output_file: Path):
        """生成模块的README"""
        lines = [f"# {module_data['module_name']}\n"]
        lines.append(f"**分类**: {module_data['category']}")
        lines.append(f"**来源论文**: {module_data['paper_id']}\n")
        lines.append(f"## 核心原理\n{module_data['principle']}\n")
        lines.append(f"## 详细描述\n{module_data['description']}\n")

        if module_data.get('formula'):
            lines.append(f"## 关键公式\n```\n{module_data['formula']}\n```\n")

        if module_data.get('complexity'):
            lines.append(f"## 复杂度\n{module_data['complexity']}\n")

        if module_data.get('use_cases'):
            lines.append("## 应用场景")
            for uc in module_data['use_cases']:
                lines.append(f"- {uc}")
            lines.append("")

        if module_data.get('dependencies'):
            lines.append("## 依赖模块")
            for dep in module_data['dependencies']:
                lines.append(f"- {dep}")
            lines.append("")

        if module_data.get('github_url'):
            lines.append(f"## 代码仓库\n{module_data['github_url']}\n")

        if module_data.get('code_path'):
            lines.append(f"## 代码路径\n`{module_data['code_path']}`\n")

        output_file.write_text("\n".join(lines), encoding='utf-8')

    @staticmethod
    def read_module(paper_id: str, module_name: str) -> Dict[str, Any]:
        """
        读取模块详情

        Args:
            paper_id: 论文ID
            module_name: 模块名称

        Returns:
            模块数据
        """
        module_file = KnowledgeTools.MODULES_DIR / paper_id / module_name / "MODULE.json"

        if not module_file.exists():
            return {
                "status": "not_found",
                "paper_id": paper_id,
                "module_name": module_name,
                "message": f"Module {module_name} not found in {paper_id}"
            }

        with open(module_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return {
            "status": "success",
            "data": data
        }

    @staticmethod
    def list_modules(
        category: Optional[str] = None,
        paper_id: Optional[str] = None,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        列出模块（可筛选）

        Args:
            category: 按分类筛选
            paper_id: 按论文筛选
            limit: 返回数量限制

        Returns:
            模块列表
        """
        KnowledgeTools._ensure_dirs()

        all_modules = []

        # 遍历所有MODULE.json
        for module_file in KnowledgeTools.MODULES_DIR.glob("*/*/MODULE.json"):
            with open(module_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 筛选
            if category and data.get("category") != category:
                continue
            if paper_id and data.get("paper_id") != paper_id:
                continue

            all_modules.append({
                "paper_id": data["paper_id"],
                "module_name": data["module_name"],
                "category": data.get("category"),
                "principle": data.get("principle", ""),
                "code_path": data.get("code_path"),
                "created_at": data.get("created_at", "")
            })

        # 按创建时间倒序
        all_modules.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        return {
            "status": "success",
            "total": len(all_modules),
            "modules": all_modules[:limit]
        }

    @staticmethod
    def _update_module_index():
        """更新全局模块索引"""
        index_file = KnowledgeTools.MODULES_DIR / "MODULE_INDEX.md"

        all_modules = []
        for module_file in KnowledgeTools.MODULES_DIR.glob("*/*/MODULE.json"):
            with open(module_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            all_modules.append({
                "paper_id": data["paper_id"],
                "module_name": data["module_name"],
                "category": data.get("category", "Unknown"),
                "principle": data.get("principle", ""),
                "created_at": data.get("created_at", "")
            })

        # 按分类和时间组织
        by_category = {}
        for mod in all_modules:
            cat = mod["category"]
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(mod)

        lines = ["# 模块索引\n"]
        for cat in ["Core_Mechanisms", "Efficiency", "Architecture", "Unknown"]:
            if cat in by_category:
                lines.append(f"## {cat}\n")
                for mod in sorted(by_category[cat], key=lambda x: x["created_at"], reverse=True):
                    lines.append(f"### {mod['module_name']} (来源: {mod['paper_id']})")
                    lines.append(f"- **原理**: {mod['principle'][:100]}...")
                    lines.append(f"- **路径**: `modules/{mod['paper_id']}/{mod['module_name']}/`\n")

        index_file.write_text("\n".join(lines), encoding='utf-8')


# 导出工具函数供MCP使用
def add_insight(**kwargs):
    """添加研究洞察"""
    return KnowledgeTools.add_insight(**kwargs)

def read_insight(paper_id: str):
    """读取洞察"""
    return KnowledgeTools.read_insight(paper_id)

def list_insights(**kwargs):
    """列出洞察"""
    return KnowledgeTools.list_insights(**kwargs)

def add_module(**kwargs):
    """添加技术模块"""
    return KnowledgeTools.add_module(**kwargs)

def read_module(paper_id: str, module_name: str):
    """读取模块"""
    return KnowledgeTools.read_module(paper_id, module_name)

def list_modules(**kwargs):
    """列出模块"""
    return KnowledgeTools.list_modules(**kwargs)

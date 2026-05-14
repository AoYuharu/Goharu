"""
PaperAnalysisOrchestrator - 论文分析主智能体

负责协调 7 个子智能体，按照 SOP 流程完成论文分析任务。
"""

import json
import re
import time
from pathlib import Path
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

from Agent.Delegates.PaperAnalysisDelegate import PaperAnalysisDelegate
from Agent.LargeLanguageModel import LargeLanguageModel
from configurationLoader import config


class PaperAnalysisOrchestrator:
    """论文分析主智能体 - 协调器"""

    @staticmethod
    def _load_orchestrator_prompt() -> str:
        """从文件加载主智能体提示词"""
        prompt_path = Path(__file__).parent.parent / "prompts" / "paper_analysis" / "orchestrator.md"
        try:
            return prompt_path.read_text(encoding='utf-8')
        except FileNotFoundError:
            raise FileNotFoundError(f"提示词文件未找到: {prompt_path}")

    # 主智能体的 SOP 提示词
    ORCHESTRATOR_SYSTEM_PROMPT = property(lambda self: self._load_orchestrator_prompt())

    def __init__(self, tools_registry, output_callback=None):
        """
        初始化主智能体

        Args:
            tools_registry: 工具注册表实例
            output_callback: 输出回调函数
        """
        self.tools_registry = tools_registry
        self.output_callback = output_callback
        self.llm = LargeLanguageModel()

        # 执行统计
        self.start_time = None
        self.end_time = None
        self.total_token_count = 0
        self.stage_results = {}

    def _notify(self, message: str, level: str = "info"):
        """通知输出消息"""
        if self.output_callback:
            self.output_callback(message, level)
        else:
            # 移除emoji和特殊字符避免Windows编码问题
            clean_message = message.encode('ascii', 'ignore').decode('ascii')
            try:
                print(f"[{level.upper()}] {clean_message}")
            except UnicodeEncodeError:
                print(f"[{level.upper()}] [encoding error]")

    @staticmethod
    def _extract_json(text: str) -> str:
        """
        从可能包含 Markdown 代码块的文本中提取 JSON 字符串

        Args:
            text: 可能包含 Markdown 包裹的文本

        Returns:
            纯净的 JSON 字符串
        """
        if not text or not text.strip():
            return text

        text = text.strip()

        # 去掉 ```json 和 ``` 包裹
        json_pattern = r'```(?:json)?\s*\n?(.*?)\n?```'
        match = re.search(json_pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # 直接是 JSON 对象（{...}）
        if text.startswith('{') and text.endswith('}'):
            return text

        # 包含 JSON 对象在文本中
        brace_match = re.search(r'\{.*\}', text, re.DOTALL)
        if brace_match:
            return brace_match.group(0)

        return text

    @staticmethod
    def _safe_parse_json(text: str, fallback: dict = None) -> dict:
        """
        安全解析 JSON，失败时返回 fallback

        Args:
            text: 可能包含 JSON 的文本
            fallback: 解析失败时的默认值

        Returns:
            解析后的字典
        """
        if fallback is None:
            fallback = {}
        try:
            extracted = PaperAnalysisOrchestrator._extract_json(text)
            if not extracted or not extracted.strip():
                return fallback
            return json.loads(extracted)
        except (json.JSONDecodeError, ValueError):
            return fallback

    def _create_delegate(self, agent_type: str, task: str, agent_id: str) -> Dict[str, Any]:
        """
        创建并执行子智能体

        Args:
            agent_type: 子智能体类型
            task: 任务描述
            agent_id: 唯一标识符

        Returns:
            子智能体的执行结果
        """
        self._notify(f"🚀 启动子智能体: {agent_id} ({agent_type})", "info")

        delegate = PaperAnalysisDelegate(
            agent_type=agent_type,
            task=task,
            agent_id=agent_id,
            tools_registry=self.tools_registry,
            output_callback=self.output_callback
        )

        result = delegate.execute()

        # 累加 token 数量
        self.total_token_count += result.get("token_count", 0)

        return result

    def analyze_paper(self, pdf_path: str) -> Dict[str, Any]:
        """
        执行完整的论文分析流程

        Args:
            pdf_path: PDF 文件路径

        Returns:
            分析结果字典
        """
        self.start_time = time.time()

        # 预创建所需的目录结构
        for dir_path in [
            "runtime_memory/context",
            "runtime_memory/modules",
            "runtime_memory/insights",
            "runtime_memory/papers/temp",
            "runtime_memory/temp",
        ]:
            Path(dir_path).mkdir(parents=True, exist_ok=True)
        self._notify(f"\n{'='*60}", "info")
        self._notify(f"📄 开始分析论文: {pdf_path}", "info")
        self._notify(f"{'='*60}\n", "info")

        try:
            # ========== 阶段 1: PDF 解析 ==========
            self._notify("🔹 阶段 1: PDF 解析", "info")
            pdf_result = self._create_delegate(
                agent_type="pdf_parser",
                task=f"从 {pdf_path} 中提取所有原始材料，包括元数据、章节文本、图表、公式和引用列表。",
                agent_id="pdf_parser_001"
            )

            if pdf_result["status"] != "success":
                raise Exception(f"PDF 解析失败: {pdf_result.get('error')}")

            self.stage_results["pdf_parser"] = pdf_result
            self._notify(f"✅ 阶段 1 完成 (耗时: {pdf_result['duration_ms']}ms)", "info")
            self._notify(f"   - 提取了章节、图表、公式和引用\n", "info")

            # 解析 PDF 结果 - 从文件中读取
            result_json = json.loads(pdf_result["content"])
            result_file = result_json.get("result_file")
            if not result_file:
                raise Exception("PDF 解析结果中缺少 result_file 字段")

            with open(result_file, 'r', encoding='utf-8') as f:
                pdf_data = json.load(f)

            # ========== 阶段 2: 内容分析 ==========
            self._notify("🔹 阶段 2: 内容分析", "info")
            content_result = self._create_delegate(
                agent_type="content_analysis",
                task=f"深度分析以下论文章节：\n\n{json.dumps(pdf_data.get('sections', {}), ensure_ascii=False, indent=2)}",
                agent_id="content_analysis_001"
            )

            if content_result["status"] != "success":
                raise Exception(f"内容分析失败: {content_result.get('error')}")

            self.stage_results["content_analysis"] = content_result
            self._notify(f"✅ 阶段 2 完成 (耗时: {content_result['duration_ms']}ms)", "info")
            self._notify(f"   - 分析了摘要、引言、方法、实验、结论\n", "info")

            content_data = self._safe_parse_json(content_result["content"], {})

            # ========== 阶段 3: 并行执行（技术提取 + 假数据复现 + 相关文献）==========
            self._notify("🔹 阶段 3: 技术提取 + 假数据复现 + 相关文献（并行）", "info")

            with ThreadPoolExecutor(max_workers=3) as executor:
                # 3.1 技术提取
                tech_future = executor.submit(
                    self._create_delegate,
                    "tech_extraction",
                    f"从以下方法论分析中提取技术模块和创新点：\n\n{json.dumps(content_data.get('method_analysis', {}), ensure_ascii=False, indent=2)}",
                    "tech_extraction_001"
                )

                # 3.2 假数据复现
                repro_future = executor.submit(
                    self._create_delegate,
                    "fake_data_reproduction",
                    f"基于以下方法论生成假数据并追踪维度变化：\n\n方法论: {json.dumps(content_data.get('method_analysis', {}), ensure_ascii=False, indent=2)}\n\nGitHub 链接: {pdf_data.get('metadata', {}).get('github_url', 'None')}",
                    "fake_data_reproduction_001"
                )

                # 3.3 相关文献分析
                literature_future = executor.submit(
                    self._create_delegate,
                    "literature_analysis",
                    f"分析以下引用文献和 Related Work：\n\n引用列表: {json.dumps(pdf_data.get('references', []), ensure_ascii=False, indent=2)}\n\nRelated Work: {pdf_data.get('sections', {}).get('related_work', 'None')}",
                    "literature_analysis_001"
                )

            # 获取并行结果
            tech_result = tech_future.result()
            repro_result = repro_future.result()
            literature_result = literature_future.result()

            self.stage_results["tech_extraction"] = tech_result
            self.stage_results["fake_data_reproduction"] = repro_result
            self.stage_results["literature_analysis"] = literature_result

            self._notify(f"✅ 阶段 3 完成", "info")
            self._notify(f"   - 技术提取: {tech_result['duration_ms']}ms", "info")
            self._notify(f"   - 假数据复现: {repro_result['duration_ms']}ms", "info")
            self._notify(f"   - 相关文献: {literature_result['duration_ms']}ms\n", "info")

            tech_data = self._safe_parse_json(tech_result["content"], {}) if tech_result["status"] == "success" else {}
            repro_data = self._safe_parse_json(repro_result["content"], {}) if repro_result["status"] == "success" else {}
            literature_data = self._safe_parse_json(literature_result["content"], {}) if literature_result["status"] == "success" else {}

            # ========== 阶段 4: 关联分析 ==========
            self._notify("🔹 阶段 4: 关联分析", "info")
            relation_result = self._create_delegate(
                agent_type="relation_analysis",
                task=f"基于以下信息进行关联分析：\n\n引用分析: {json.dumps(literature_data, ensure_ascii=False, indent=2)}\n\n实验结果: {json.dumps(content_data.get('experiments_analysis', {}), ensure_ascii=False, indent=2)}\n\n论文摘要: {pdf_data.get('sections', {}).get('abstract', '')}",
                agent_id="relation_analysis_001"
            )

            if relation_result["status"] != "success":
                raise Exception(f"关联分析失败: {relation_result.get('error')}")

            self.stage_results["relation_analysis"] = relation_result
            self._notify(f"✅ 阶段 4 完成 (耗时: {relation_result['duration_ms']}ms)", "info")
            self._notify(f"   - 构建了引用网络、检索了相似论文、判断了 SOTA 状态\n", "info")

            relation_data = self._safe_parse_json(relation_result["content"], {})

            # ========== 阶段 5: 知识库整合（程序化执行，避免 MiniMax Edit 限制）==========
            self._notify("🔹 阶段 5: 知识库整合", "info")
            t5_start = time.time()

            paper_id = "paper_001"
            integration_data = self._integrate_knowledge(
                paper_id=paper_id,
                pdf_data=pdf_data,
                content_data=content_data,
                tech_data=tech_data,
                repro_data=repro_data,
                literature_data=literature_data,
                relation_data=relation_data
            )

            t5_duration = int((time.time() - t5_start) * 1000)
            self.stage_results["knowledge_integration"] = {
                "status": "success",
                "duration_ms": t5_duration,
                "content": json.dumps(integration_data, ensure_ascii=False)
            }
            self._notify(f"✅ 阶段 5 完成 (耗时: {t5_duration}ms)", "info")
            self._notify(f"   - 更新了知识库索引和论文总览\n", "info")

            # ========== 生成最终报告 ==========
            self.end_time = time.time()
            total_duration = int((self.end_time - self.start_time) * 1000)

            self._notify(f"\n{'='*60}", "info")
            self._notify(f"🎉 论文分析完成！", "info")
            self._notify(f"{'='*60}\n", "info")

            # 生成报告
            report = self._generate_report(pdf_data, tech_data, relation_data, integration_data, total_duration)
            self._notify(report, "info")

            return {
                "status": "success",
                "pdf_path": pdf_path,
                "paper_id": paper_id,
                "total_duration_ms": total_duration,
                "total_token_count": self.total_token_count,
                "total_tokens": self.total_token_count,
                "stage_results": self.stage_results,
                "stages_completed": list(self.stage_results.keys()),
                "report": report
            }

        except Exception as e:
            self.end_time = time.time()
            self._notify(f"\n❌ 论文分析失败: {str(e)}", "error")

            return {
                "status": "error",
                "pdf_path": pdf_path,
                "error": str(e),
                "total_duration_ms": int((self.end_time - self.start_time) * 1000) if self.end_time else 0,
                "total_token_count": self.total_token_count,
                "stage_results": self.stage_results
            }

    def _integrate_knowledge(self, paper_id: str, pdf_data: Dict, content_data: Dict,
                              tech_data: Dict, repro_data: Dict, literature_data: Dict,
                              relation_data: Dict) -> Dict[str, Any]:
        """
        使用知识管理工具整合知识库

        通过AddInsight和AddModule工具来结构化存储论文分析结果
        按照论文章节结构组织目录：abstract/, introduction/, relatedwork/, methods/, experiments/, conclusion/
        """
        from Tools.builtin.knowledge_tools import add_insight, add_module

        metadata = pdf_data.get("metadata", {}) or {}
        title = metadata.get("title") or pdf_data.get("title", "Unknown")
        authors = metadata.get("authors", [])
        year = metadata.get("year", "Unknown")
        venue = metadata.get("venue", "Unknown")

        # 创建论文基础目录结构 - 按章节组织
        base_dir = Path("runtime_memory") / "papers" / paper_id
        section_dirs = ["abstract", "introduction", "relatedwork", "methods", "experiments", "conclusion",
                       "citations", "innovations", "repository"]
        for sub_dir in section_dirs:
            (base_dir / sub_dir).mkdir(parents=True, exist_ok=True)

        # 获取章节内容
        sections = pdf_data.get('sections', {})

        # === 1. abstract/content.md ===
        abstract_text = sections.get('abstract', content_data.get('abstract_analysis', {}).get('summary', '待补充'))
        if isinstance(abstract_text, dict):
            abstract_text = json.dumps(abstract_text, ensure_ascii=False, indent=2)
        (base_dir / "abstract" / "content.md").write_text(f"# Abstract\n\n{abstract_text}", encoding='utf-8')

        # === 2. introduction/content.md ===
        intro_text = sections.get('introduction', '')
        intro_analysis = content_data.get('introduction_analysis', {})
        intro_content = f"""# Introduction

## 原文内容
{intro_text}

## 分析
- **研究问题**: {intro_analysis.get('problem', '待补充')}
- **研究动机**: {intro_analysis.get('motivation', '待补充')}
- **主要贡献**: {', '.join(intro_analysis.get('contributions', [])) if intro_analysis.get('contributions') else '待补充'}
"""
        (base_dir / "introduction" / "content.md").write_text(intro_content, encoding='utf-8')

        # === 3. relatedwork/content.md ===
        related_work_text = sections.get('related_work', sections.get('relatedwork', ''))
        related_work_summary = literature_data.get('related_work_summary', '')
        rw_content = f"""# Related Work

## 原文内容
{related_work_text}

## 分析总结
{related_work_summary}
"""
        (base_dir / "relatedwork" / "content.md").write_text(rw_content, encoding='utf-8')

        # === 4. methods/content.md ===
        method_text = sections.get('method', sections.get('methods', sections.get('methodology', '')))
        method_analysis = content_data.get('method_analysis', {})
        method_content = f"""# Methods

## 原文内容
{method_text}

## 架构分析
{method_analysis.get('architecture', '待补充')}

## 核心组件
"""
        core_components = method_analysis.get('core_components', [])
        if core_components:
            for comp in core_components:
                if isinstance(comp, dict):
                    method_content += f"\n### {comp.get('name', 'Unknown')}\n"
                    method_content += f"- **公式**: {comp.get('formula', 'N/A')}\n"
                    method_content += f"- **作用**: {comp.get('purpose', 'N/A')}\n"
        else:
            method_content += "\n待补充\n"

        method_content += f"\n## 关键创新点\n"
        key_innovations = method_analysis.get('key_innovations', [])
        if key_innovations:
            for inno in key_innovations:
                method_content += f"- {inno}\n"
        else:
            method_content += "待补充\n"

        (base_dir / "methods" / "content.md").write_text(method_content, encoding='utf-8')

        # === 5. experiments/content.md ===
        exp_text = sections.get('experiments', sections.get('experiment', sections.get('results', '')))
        exp_analysis = content_data.get('experiments_analysis', {})
        exp_content = f"""# Experiments

## 原文内容
{exp_text}

## 实验设置
- **数据集**: {', '.join(exp_analysis.get('datasets', [])) if exp_analysis.get('datasets') else '待补充'}
- **Baselines**: {', '.join(exp_analysis.get('baselines', [])) if exp_analysis.get('baselines') else '待补充'}

## 实验结果
"""
        results = exp_analysis.get('results', {})
        if results:
            exp_content += json.dumps(results, ensure_ascii=False, indent=2) + "\n"
        else:
            exp_content += "待补充\n"

        exp_content += "\n## 消融实验\n"
        ablation = exp_analysis.get('ablation_studies', [])
        if ablation:
            for ab in ablation:
                if isinstance(ab, dict):
                    exp_content += f"- 移除 {ab.get('removed', 'N/A')}: {ab.get('impact', 'N/A')}\n"
        else:
            exp_content += "待补充\n"

        (base_dir / "experiments" / "content.md").write_text(exp_content, encoding='utf-8')

        # === 6. conclusion/content.md ===
        conclusion_text = sections.get('conclusion', sections.get('conclusions', ''))
        conclusion_analysis = content_data.get('conclusion_analysis', {})
        conclusion_content = f"""# Conclusion

## 原文内容
{conclusion_text}

## 总结
{conclusion_analysis.get('summary', '待补充')}

## 局限性
"""
        limitations = conclusion_analysis.get('limitations', [])
        if limitations:
            for lim in limitations:
                conclusion_content += f"- {lim}\n"
        else:
            conclusion_content += "待补充\n"

        conclusion_content += "\n## 未来工作\n"
        future_work = conclusion_analysis.get('future_work', [])
        if future_work:
            for fw in future_work:
                conclusion_content += f"- {fw}\n"
        else:
            conclusion_content += "待补充\n"

        (base_dir / "conclusion" / "content.md").write_text(conclusion_content, encoding='utf-8')

        # === 7. citations/references.json ===
        refs = literature_data.get('key_citations', literature_data.get('citations', []))
        if not refs:
            refs = pdf_data.get('references', [])
        citations = {"citations": refs, "paper_id": paper_id, "title": title}
        (base_dir / "citations" / "references.json").write_text(
            json.dumps(citations, ensure_ascii=False, indent=2), encoding='utf-8'
        )

        # === 8. innovations/innovations.md ===
        innovations = tech_data.get('innovations', [])
        innovation_lines = ["# 核心创新点\n"]
        if innovations:
            for i, inno in enumerate(innovations, 1):
                if isinstance(inno, dict):
                    innovation_lines.append(f"## 创新点 {i}: {inno.get('title', inno.get('name', ''))}")
                    innovation_lines.append(f"\n{inno.get('description', '')}")
                    innovation_lines.append(f"\n**影响**: {inno.get('impact', 'N/A')}\n")
                else:
                    innovation_lines.append(f"## 创新点 {i}")
                    innovation_lines.append(f"{inno}\n")
        else:
            innovation_lines.append("待补充\n")
        (base_dir / "innovations" / "innovations.md").write_text("\n".join(innovation_lines), encoding='utf-8')

        # === 9. repository/info.md ===
        github_url = metadata.get('github_url', '')
        repo_content = f"""# 代码仓库信息

## GitHub 链接
{github_url if github_url else '未提供'}

## 维度流分析
"""
        dimension_flow = repro_data.get('dimension_flow', [])
        if dimension_flow:
            repo_content += json.dumps(dimension_flow, ensure_ascii=False, indent=2) + "\n"
        else:
            repo_content += "待补充\n"

        repo_content += "\n## 流程图\n"
        flow_diagram = repro_data.get('flow_diagram_mermaid', '')
        if flow_diagram:
            repo_content += f"```mermaid\n{flow_diagram}\n```\n"
        else:
            repo_content += "待补充\n"

        (base_dir / "repository" / "info.md").write_text(repo_content, encoding='utf-8')

        # === 10. 论文总览 summary.md ===
        summary_content = f"""# {title}

## 基本信息
- **标题**: {title}
- **作者**: {', '.join(authors) if authors else 'Unknown'}
- **年份**: {year}
- **会议/期刊**: {venue}

## 目录结构
- `abstract/` - 摘要
- `introduction/` - 引言
- `relatedwork/` - 相关工作
- `methods/` - 方法论
- `experiments/` - 实验结果
- `conclusion/` - 结论
- `citations/` - 引用文献
- `innovations/` - 核心创新点
- `repository/` - 代码仓库信息

## 关键结果
- 提取模块: {len(tech_data.get('modules', []))} 个
- 识别创新点: {len(tech_data.get('innovations', []))} 个
- SOTA 状态: {'是' if relation_data.get('sota_status', {}).get('is_sota') else '否'}

## 分析日期
{time.strftime('%Y-%m-%d')}
"""
        (base_dir / "summary.md").write_text(summary_content.strip(), encoding='utf-8')

        # === 11. 使用AddInsight工具添加创新点 ===
        insight_count = 0
        for inno in innovations:
            if isinstance(inno, dict):
                try:
                    # 推断分类
                    inno_title = inno.get('title', inno.get('name', ''))
                    inno_desc = inno.get('description', '')
                    inno_impact = inno.get('impact', '提升了模型性能')

                    # 根据内容推断category
                    category = "general"
                    if any(kw in inno_title.lower() or kw in inno_desc.lower()
                           for kw in ['architecture', 'layer', 'block', 'connection']):
                        category = "architecture"
                    elif any(kw in inno_title.lower() or kw in inno_desc.lower()
                             for kw in ['training', 'optimization', 'loss']):
                        category = "training"
                    elif any(kw in inno_title.lower() or kw in inno_desc.lower()
                             for kw in ['method', 'algorithm', 'mechanism']):
                        category = "method"

                    add_insight(
                        paper_id=paper_id,
                        title=inno_title,
                        description=inno_desc,
                        impact=inno_impact,
                        category=category,
                        tags=[],
                        related_papers=[]
                    )
                    insight_count += 1
                except Exception as e:
                    self._notify(f"⚠️  添加洞察失败: {e}", "warning")

        # === 12. 使用AddModule工具添加技术模块 ===
        modules = tech_data.get('modules', [])
        module_count = 0
        for mod in modules:
            if isinstance(mod, dict):
                try:
                    mod_name = mod.get('name', '')
                    mod_category = mod.get('category', 'Core_Mechanisms')
                    mod_principle = mod.get('principle', '')
                    mod_desc = mod.get('description', mod_principle)
                    mod_formula = mod.get('formula')
                    mod_complexity = mod.get('complexity')
                    mod_use_cases = mod.get('use_cases', [])

                    # 确保category是有效值
                    if mod_category not in ['Core_Mechanisms', 'Efficiency', 'Architecture']:
                        mod_category = 'Core_Mechanisms'

                    add_module(
                        paper_id=paper_id,
                        module_name=mod_name,
                        category=mod_category,
                        principle=mod_principle,
                        description=mod_desc,
                        formula=mod_formula,
                        complexity=mod_complexity,
                        code_path=None,
                        github_url=metadata.get('github_url'),
                        use_cases=mod_use_cases,
                        dependencies=[]
                    )
                    module_count += 1
                except Exception as e:
                    self._notify(f"⚠️  添加模块失败: {e}", "warning")

        # === 13. 更新 MEMORY.md ===
        module_names = [m.get('name', str(m)) if isinstance(m, dict) else str(m) for m in modules]
        memory_content = f"# 全局记忆索引\n\n## 论文库概览\n\n| Paper ID | Title | Year | Key Module | SOTA |\n|----------|-------|------|------------|------|\n| {paper_id} | {title.upper()} | {year} | {module_names[0] if module_names else 'TBD'} | {'yes' if relation_data.get('sota_status', {}).get('is_sota') else 'no'} |\n\n## 最近更新\n- {time.strftime('%Y-%m-%d')}: {paper_id} {title.upper()}\n"
        Path("runtime_memory/context/MEMORY.md").write_text(memory_content, encoding='utf-8')

        # === 14. 更新 CURRENT_FOCUS.md ===
        focus_content = f"# 当前关注点\n\n## 正在分析的论文\n- {paper_id}: {title}\n- 分析日期: {time.strftime('%Y-%m-%d')}\n- 状态: 已完成\n"
        Path("runtime_memory/context/CURRENT_FOCUS.md").write_text(focus_content, encoding='utf-8')

        # === 15. 更新 SOTA_SNAPSHOT.md ===
        is_sota = relation_data.get('sota_status', {}).get('is_sota', False)
        if is_sota:
            sota_content = f"# SOTA 快照\n\n## 当前 SOTA\n- {paper_id}: {title} ({year})\n- 更新时间: {time.strftime('%Y-%m-%d')}\n"
            Path("runtime_memory/context/SOTA_SNAPSHOT.md").write_text(sota_content, encoding='utf-8')

        # 构建整合数据
        updated_files = [
            f"runtime_memory/papers/{paper_id}/summary.md",
            f"runtime_memory/papers/{paper_id}/abstract/content.md",
            f"runtime_memory/papers/{paper_id}/introduction/content.md",
            f"runtime_memory/papers/{paper_id}/relatedwork/content.md",
            f"runtime_memory/papers/{paper_id}/methods/content.md",
            f"runtime_memory/papers/{paper_id}/experiments/content.md",
            f"runtime_memory/papers/{paper_id}/conclusion/content.md",
            f"runtime_memory/papers/{paper_id}/citations/references.json",
            f"runtime_memory/papers/{paper_id}/innovations/innovations.md",
            f"runtime_memory/papers/{paper_id}/repository/info.md",
            "runtime_memory/context/MEMORY.md",
            "runtime_memory/context/CURRENT_FOCUS.md",
        ]
        if is_sota:
            updated_files.append("runtime_memory/context/SOTA_SNAPSHOT.md")

        return {
            "updated_files": updated_files,
            "summary": {
                "paper_id": paper_id,
                "title": title,
                "new_modules": module_count,
                "new_insights": insight_count,
                "is_sota": is_sota
            }
        }

    def _generate_report(self, pdf_data: Dict, tech_data: Dict, relation_data: Dict, integration_data: Dict, total_duration: int) -> str:
        """生成最终报告"""
        metadata = pdf_data.get("metadata", {})
        summary = integration_data.get("summary", {})
        paper_id = summary.get('paper_id') or 'paper_001'

        authors = metadata.get('authors', [])
        author_str = ', '.join(authors) if authors else 'Unknown'
        title = metadata.get('title', 'Unknown') or 'Unknown'
        year = metadata.get('year', 'Unknown') or 'Unknown'
        venue = metadata.get('venue', 'Unknown') or 'Unknown'

        modules_count = len(tech_data.get('modules', []))
        innovations_count = len(tech_data.get('innovations', []))
        similar_count = len(relation_data.get('similar_papers', []))
        is_sota = relation_data.get('sota_status', {}).get('is_sota', False)

        report_lines = []
        report_lines.append("")
        report_lines.append("## 论文信息")
        report_lines.append(f"- 标题: {title}")
        report_lines.append(f"- 作者: {author_str}")
        report_lines.append(f"- 年份: {year}")
        report_lines.append(f"- 会议/期刊: {venue}")
        report_lines.append("")
        report_lines.append("## 分析结果")
        report_lines.append(f"- 提取模块: {modules_count} 个")
        report_lines.append(f"- 识别创新点: {innovations_count} 个")
        report_lines.append(f"- 相似论文: {similar_count} 篇")
        report_lines.append(f"- SOTA 状态: {'是' if is_sota else '否'}")
        report_lines.append("")
        report_lines.append("## 生成文件")
        report_lines.append(f"- 论文总览: papers/{paper_id}/summary.md")
        report_lines.append(f"- 摘要: papers/{paper_id}/abstract/content.md")
        report_lines.append(f"- 引言: papers/{paper_id}/introduction/content.md")
        report_lines.append(f"- 相关工作: papers/{paper_id}/relatedwork/content.md")
        report_lines.append(f"- 方法论: papers/{paper_id}/methods/content.md")
        report_lines.append(f"- 实验: papers/{paper_id}/experiments/content.md")
        report_lines.append(f"- 结论: papers/{paper_id}/conclusion/content.md")
        report_lines.append(f"- 创新点: papers/{paper_id}/innovations/innovations.md")
        report_lines.append(f"- 代码仓库: papers/{paper_id}/repository/info.md")
        report_lines.append("")
        report_lines.append("## 执行统计")
        report_lines.append(f"- 总耗时: {total_duration} ms ({total_duration / 1000:.2f} 秒)")
        report_lines.append(f"- 总 Token 数: {self.total_token_count}")
        report_lines.append("")
        report_lines.append(f"查看论文总览: Read papers/{paper_id}/summary.md")

        return "\n".join(report_lines)

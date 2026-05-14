"""
PDF 工具 - 提供 PDF 解析功能

标准工具注册流程：
1. 定义工具函数
2. 使用 registry.register() 注册
3. 在 core_tools.py 中导入以自动注册
"""

import json
import signal
from pathlib import Path
from Tools.registry import registry


class TimeoutError(Exception):
    """PDF 解析超时"""
    pass


def _timeout_handler(signum, frame):
    raise TimeoutError("PDF parsing timed out")


def parse_pdf(pdf_path: str, output_dir: str = "runtime_memory/papers/temp") -> str:
    """
    解析 PDF 文件并返回结构化数据

    Args:
        pdf_path: PDF 文件路径（相对或绝对路径）
        output_dir: 输出目录，用于保存提取的图片（默认：runtime_memory/papers/temp）

    Returns:
        JSON 字符串，包含：
        - result_file: 解析结果保存的文件路径
        - metadata: 元数据（标题、作者、年份、页数）
        - sections: 章节列表（名称及内容长度）
        - figures_count: 提取的图表数量
        - tables_count: 提取的表格数量

    Raises:
        FileNotFoundError: PDF 文件不存在
        Exception: 解析过程中的其他错误
    """
    try:
        from Tools.pdf_parser import PDFParser

        # 检查文件存在
        pdf_path_obj = Path(pdf_path).resolve()
        if not pdf_path_obj.exists():
            return json.dumps({
                "status": "error",
                "error": f"PDF file not found: {pdf_path}",
            }, ensure_ascii=False)

        # 设置超时（180秒），Windows 上 signal.alarm 不可用则跳过
        TIMEOUT_SECONDS = 180
        old_handler = None
        try:
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(TIMEOUT_SECONDS)
        except (AttributeError, ValueError):
            pass  # Windows 不支持 SIGALRM，跳过超时设置

        try:
            # 直接用 PDFParser（不启用 OCR，速度更快）
            parser = PDFParser(use_ocr=False)
            document = parser.parse(str(pdf_path_obj), output_dir=output_dir)

            # 保存完整结果到文件（一次性序列化）
            temp_dir = Path("runtime_memory/temp")
            temp_dir.mkdir(parents=True, exist_ok=True)
            result_file = temp_dir / "pdf_parse_result.json"
            full_dict = document.to_dict()
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(full_dict, f, ensure_ascii=False, indent=2)

            # 从 document 对象直接提取元数据和摘要（不再做 json.loads 往返）
            metadata = full_dict.get("metadata", {})
            sections_summary = {
                name: len(sec.get("content", ""))
                for name, sec in full_dict.get("sections", {}).items()
            }

            return json.dumps({
                "status": "success",
                "result_file": str(result_file),
                "metadata": metadata,
                "sections": sections_summary,
                "figures_count": len(full_dict.get("figures", [])),
                "tables_count": len(full_dict.get("tables", [])),
                "full_text_length": len(full_dict.get("full_text", "")),
            }, ensure_ascii=False)

        finally:
            # 取消超时
            try:
                signal.alarm(0)
                if old_handler is not None:
                    signal.signal(signal.SIGALRM, old_handler)
            except (AttributeError, ValueError):
                pass

    except TimeoutError:
        return json.dumps({
            "status": "error",
            "error": f"PDF parsing timed out after {TIMEOUT_SECONDS}s. The file may be too large or corrupted.",
        }, ensure_ascii=False)

    except FileNotFoundError as e:
        return json.dumps({
            "status": "error",
            "error": f"PDF file not found: {pdf_path}",
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": f"Failed to parse PDF: {str(e)}",
        }, ensure_ascii=False)


# 注册工具
registry.register(
    name="parse_pdf",
    description="Parse an academic PDF and extract structured content (title, authors, sections, figures, tables, formulas, references). Full result saved to runtime_memory/temp/pdf_parse_result.json.",
    arguments_schema={
        "type": "object",
        "properties": {
            "pdf_path": {
                "type": "string",
                "description": "PDF file path, relative or absolute (e.g. 'essay/paper.pdf').",
            },
            "output_dir": {
                "type": "string",
                "description": "Directory to save extracted images. Default: runtime_memory/papers/temp.",
            },
        },
        "required": ["pdf_path"],
    },
    handler=parse_pdf,
    group="pdf",
)

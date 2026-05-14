"""
System Info Tool - cross-platform hardware configuration detection
"""
import json

from Tools.registry import registry
from Tools.platform_utils import collect_system_info, get_platform_name


def get_system_info(target: str = "all") -> str:
    """
    Get system hardware information (cross-platform).

    Args:
        target: Query type
            - "all": All information
            - "cpu": CPU only
            - "gpu": GPU only
            - "memory": Memory only
    """
    results = {
        "status": "success",
        "platform": get_platform_name(),
        "data": collect_system_info(target),
    }
    return json.dumps(results, ensure_ascii=False, indent=2)


# Register tool
registry.register(
    name="get_system_info",
    description="Get hardware configuration and system information (CPU, GPU, memory). Cross-platform, returns JSON.",
    arguments_schema={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Query scope: all / cpu / gpu / memory.",
                "enum": ["all", "cpu", "gpu", "memory"],
                "default": "all"
            }
        }
    },
    handler=get_system_info,
    group="system"
)

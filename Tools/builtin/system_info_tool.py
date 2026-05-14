"""
System Info Tool - 获取电脑硬件配置信息
"""
import subprocess
import json

from Tools.registry import registry



def get_system_info(target: str = "all") -> str:
    """
    获取电脑系统信息
    
    参数:
        target: 查询类型
            - "all": 返回所有信息
            - "cpu": 仅CPU信息
            - "gpu": 仅GPU信息
            - "memory": 仅内存信息
    """
    results = {"status": "success", "data": {}}
    
    if target in ["all", "cpu"]:
        # CPU信息
        cpu_output = subprocess.getoutput("wmic cpu get Name,NumberOfCores,MaxClockSpeed /format:list")
        results["data"]["cpu"] = cpu_output
    
    if target in ["all", "gpu"]:
        # GPU信息 - 使用简化命令避免安全拦截
        gpu_output = subprocess.getoutput("nvidia-smi -L")
        if "NVIDIA" in gpu_output or "GPU" in gpu_output:
            results["data"]["gpu"] = gpu_output
        else:
            results["data"]["gpu"] = "No NVIDIA GPU found"
    
    if target in ["all", "memory"]:
        # 内存信息
        mem_output = subprocess.getoutput("systeminfo | findstr /B \"物理内存总量\"")
        results["data"]["memory"] = mem_output if mem_output.strip() else "Memory info unavailable"
    
    return json.dumps(results, ensure_ascii=False, indent=2)



# 注册工具
registry.register(
    name="get_system_info",
    description="Get hardware configuration and system information (CPU, GPU, memory). Returns JSON.",
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

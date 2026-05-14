# Get-System-Info Tool
# 用法: powershell -File get_system_info.ps1

Write-Host "=== 电脑配置信息 ===" -ForegroundColor Cyan

# CPU信息
Write-Host "`n[CPU]" -ForegroundColor Yellow
wmic cpu get name,numberofcores,numberoflogicalprocessors /format:list 2>$null

# 内存信息
Write-Host "`n[Memory]" -ForegroundColor Yellow
wmic memphysical get Manufacturer,Model,Capacity /format:list 2>$null
wmic memorychip get Capacity,Speed /format:list 2>$null

# 显卡信息
Write-Host "`n[GPU]" -ForegroundColor Yellow
wmic path win32_VideoController get Name,AdapterRAM,DriverVersion /format:list 2>$null
nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader 2>$null

# 磁盘信息
Write-Host "`n[Disk]" -ForegroundColor Yellow
wmic diskdrive get Model,Size,MediaType /format:list 2>$null

# 系统信息
Write-Host "`n[System]" -ForegroundColor Yellow
systeminfo | findstr /B /C:"OS 名称" /C:"系统型号" /C:"处理器" /C:"物理内存总量"

Write-Host "`n=== 查询完成 ===" -ForegroundColor Cyan

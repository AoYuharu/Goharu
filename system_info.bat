@echo off
chcp 65001 >nul
echo ========================================
echo     电脑硬件配置检测脚本
echo ========================================
echo.

echo [1/7] 基础系统信息...
systeminfo | findstr /B /C:"OS 名称" /C:"OS 版本" /C:"系统类型" /C:"系统制造商" /C:"系统型号"
echo.

echo [2/7] CPU 信息...
wmic cpu get name,numberofcores,numberoflogicalprocessors,maxclockspeed,currentclockspeed /format:list
echo.

echo [3/7] 内存信息...
wmic memphysical get manufacturer,model,partnumber,capacity,speed /format:list
echo ---
wmic memorychip get manufacturer,partnumber,capacity,speed,devicelocator /format:list
echo.

echo [4/7] 磁盘信息...
wmic diskdrive get model,size,mediatype,status /format:list
echo ---
wmic logicaldisk get deviceid,size,freespace,drivetype,volumename /format:list
echo.

echo [5/7] 显卡信息...
wmic path win32_VideoController get name,adapterram,driverversion,status /format:list
echo.

echo [6/7] 主板信息...
wmic baseboard get manufacturer,product,version,serialnumber /format:list
echo.

echo [7/7] 电池信息...
wmic path win32_battery get name,deviceid,estimatedchargeremaining,status /format:list
echo.

echo ========================================
echo           检测完成
echo ========================================
pause

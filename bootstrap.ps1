# Goharu 一键引导（Windows）
#
# 一行安装（Win+R 粘贴回车，或 PowerShell 窗口执行，自动 clone + 进入交互式向导）：
#   powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/AoYuharu/Goharu/master/bootstrap.ps1 | iex"
#
# 用 irm | iex 而非下载后执行，是为了让向导在当前控制台读 stdin，支持交互式输入密钥。

$RepoUrl   = if ($env:GOHARU_REPO) { $env:GOHARU_REPO } else { "https://github.com/AoYuharu/Goharu.git" }
$TargetDir = if ($env:GOHARU_DIR)  { $env:GOHARU_DIR }  else { Join-Path $env:USERPROFILE "Goharu" }

function Info($m) { Write-Host "[+] $m" -ForegroundColor Green }
function Err($m)  { Write-Host "[x] $m" -ForegroundColor Red }

# ── 前置依赖检查 ──
$missing = @()
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $missing += "git          → winget install Git.Git" }
$py = $null
foreach ($cand in @("python", "python3", "py")) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $py = $cand; break }
}
if (-not $py) {
    $missing += "Python>=3.10  → winget install Python.Python.3.12"
} else {
    $verOk = & $py -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) { $missing += "Python>=3.10  → winget install Python.Python.3.12"; $py = $null }
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    $missing += "Node.js>=20  → winget install OpenJS.NodeJS.LTS"
} else {
    $nv = [int]((((node --version) 2>$null) -replace '^v','') -split '\.')[0]
    if ($nv -lt 20) { $missing += "Node.js>=20（当前 $(node --version)）  → winget install OpenJS.NodeJS.LTS" }
}

if ($missing.Count -gt 0) {
    Err "缺少依赖，请先安装（装完重跑本条命令）："
    $missing | ForEach-Object { Write-Host "    - $_" -ForegroundColor Yellow }
    return
}

# ── 已在仓库内：直接进入向导 ──
if ((Test-Path ".\install.py") -and (Test-Path ".\run_tui_ts.py")) {
    Info "已在 Goharu 仓库内，直接进入安装向导"
    & $py install.py
    return
}

# ── 克隆/更新到目标目录 ──
# 只有"有效仓库"（.git 完整 + install.py 在）才 pull；残缺 .git（删除/克隆中断
# 残留）或空目录自动清理重建；目录含其他文件则报错不动用户数据。
$isRepo = $false
if ((Test-Path (Join-Path $TargetDir ".git")) -and
    (Test-Path (Join-Path $TargetDir "install.py"))) {
    $isRepo = ((& git -C $TargetDir rev-parse --is-inside-work-tree 2>$null) -eq "true")
}
if ($isRepo) {
    Info "已存在 $TargetDir，拉取最新代码"
    git -C $TargetDir pull --ff-only | Out-Null
} elseif (Test-Path $TargetDir) {
    $left = Get-ChildItem $TargetDir -Force | Where-Object { $_.Name -ne ".git" }
    if ($left) {
        Err "$TargetDir 已存在但不是有效的 Goharu 仓库（含其他文件）；请删除该目录或设置 GOHARU_DIR 指定新目录后重跑"
        return
    }
    Info "发现残缺目录 $TargetDir，清理后重新克隆"
    Remove-Item $TargetDir -Recurse -Force
    git clone $RepoUrl $TargetDir
} else {
    Info "克隆仓库到 $TargetDir"
    git clone $RepoUrl $TargetDir
}

if (-not (Test-Path (Join-Path $TargetDir "install.py"))) {
    Err "克隆后 $TargetDir 中没有 install.py，请检查网络后重试"
    return
}
Set-Location $TargetDir
Info "进入安装向导…"
& $py install.py

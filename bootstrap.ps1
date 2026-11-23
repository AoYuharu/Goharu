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
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { $missing += "Node.js>=18  → winget install OpenJS.NodeJS" }

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
if (Test-Path (Join-Path $TargetDir ".git")) {
    Info "已存在 $TargetDir，拉取最新代码"
    git -C $TargetDir pull --ff-only | Out-Null
} else {
    Info "克隆仓库到 $TargetDir"
    git clone $RepoUrl $TargetDir
}

Set-Location $TargetDir
Info "进入安装向导…"
& $py install.py

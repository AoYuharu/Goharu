# Goharu 一键引导（Windows）
#
# 一行安装（Win+R 粘贴回车，或 PowerShell 窗口执行，自动 clone + 进入交互式向导）：
#   powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/AoYuharu/Goharu/master/bootstrap.ps1 | iex"
#
# 环境变量：GOHARU_DIR 安装目录；GOHARU_REPO 仓库地址；GOHARU_NODE_DIR Node 自动安装目录（默认 ~\node22）
#
# 组件探测原则（与 bootstrap.sh 一致）：遍历 PATH 全部候选与常见安装位置，
# 合格即用并注入 PATH；都不合格才自动安装 Node 官方二进制（npmmirror→官方
# 回退，无需管理员）；自动安装也失败才报错。
#
# 用 irm | iex 而非下载后执行，是为了让向导在当前控制台读 stdin，支持交互式输入密钥。

$RepoUrl   = if ($env:GOHARU_REPO) { $env:GOHARU_REPO } else { "https://github.com/AoYuharu/Goharu.git" }
$TargetDir = if ($env:GOHARU_DIR)  { $env:GOHARU_DIR }  else { Join-Path $env:USERPROFILE "Goharu" }
$NodeMin = 22
$NodeVersion = "v22.20.0"
$NodeHome = if ($env:GOHARU_NODE_DIR) { $env:GOHARU_NODE_DIR } else { Join-Path $env:USERPROFILE "node22" }

function Info($m) { Write-Host "[+] $m" -ForegroundColor Green }
function Err($m)  { Write-Host "[x] $m" -ForegroundColor Red }

function Test-Node($p) {
    if (-not $p -or -not (Test-Path $p)) { return $false }
    $v = (& $p --version 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $v) { return $false }
    return ([int](($v -replace '^v','') -split '\.')[0] -ge $NodeMin)
}

function Find-Node {
    $cands = @()
    $cands += (Get-Command node -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    $cands += (Join-Path $NodeHome "node.exe")
    $cands += (Get-ChildItem "$env:USERPROFILE\node*\node.exe" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    $cands += "C:\Program Files\nodejs\node.exe"
    foreach ($c in $cands) { if (Test-Node $c) { return $c } }
    return $null
}

function Install-Node {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "x64" }
    $pkg = "node-$NodeVersion-win-$arch.zip"
    $tmp = Join-Path $env:TEMP ("goharu-node-" + [guid]::NewGuid().Guid)
    New-Item -ItemType Directory -Path $tmp | Out-Null
    foreach ($base in @("https://registry.npmmirror.com/-/binary/node/$NodeVersion", "https://nodejs.org/dist/$NodeVersion")) {
        Info "下载 Node $NodeVersion（$base）"
        try {
            Invoke-WebRequest -Uri "$base/$pkg" -OutFile (Join-Path $tmp $pkg) -TimeoutSec 300 -UseBasicParsing
            Expand-Archive -Path (Join-Path $tmp $pkg) -DestinationPath $tmp -Force
            $dir = Join-Path $tmp "node-$NodeVersion-win-$arch"
            if (Test-Node (Join-Path $dir "node.exe")) {
                if (Test-Path $NodeHome) { Remove-Item $NodeHome -Recurse -Force }
                Move-Item $dir $NodeHome
                Info "Node $NodeVersion 已安装到 $NodeHome"
                Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
                return $true
            }
        } catch { Info "该源失败，换下一个" }
    }
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Err "Node 自动安装失败"
    return $false
}

# ── 前置依赖检查（全候选探测，合格即用）──
$missing = @()
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $missing += "git          → winget install Git.Git" }

$py = $null
foreach ($cand in @("python", "python3", "py")) {
    foreach ($c in (Get-Command $cand -All -ErrorAction SilentlyContinue)) {
        & $c.Source -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $c.Source; break }
    }
    if ($py) { break }
}
if (-not $py) { $missing += "Python>=3.10  → winget install Python.Python.3.12" }

$nodeBin = Find-Node
if (-not $nodeBin) {
    Info "未找到 Node.js >= $NodeMin，自动安装到 $NodeHome"
    if (Install-Node) { $nodeBin = Join-Path $NodeHome "node.exe" }
}
if ($nodeBin) {
    $env:PATH = (Split-Path $nodeBin) + ";" + $env:PATH
    Info ("Node：{0}（{1}）" -f $nodeBin, (& $nodeBin --version))
} else {
    $missing += "Node.js>=22（自动安装失败）  → winget install OpenJS.NodeJS.LTS"
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

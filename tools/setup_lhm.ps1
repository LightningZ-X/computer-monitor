# 部署 LibreHardwareMonitorLib 及其全部依赖到 packages\flat472 与 packages\flat8
#
# 为什么需要这个：LHM 是一个 .NET 程序集，靠 pythonnet 在 Python 进程内加载。
# 它有若干 NuGet 依赖（HidSharp / DiskInfoToolkit / RAMSPDToolkit-NDD ...），
# 必须解析成一套能互相找到的 DLL。本机没有 .NET SDK，所以用 nuget.exe 解析。
#
# 用法（普通权限即可，不写系统目录）：
#   pwsh -File tools\setup_lhm.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$version = "0.9.7-pre749"
$tools = Join-Path $root "tools"
$localNuget = Join-Path $root "localnuget"
$packages = Join-Path $root "packages"
$nuget = Join-Path $tools "nuget.exe"

New-Item -ItemType Directory -Force -Path $tools, $localNuget, $packages | Out-Null

if (-not (Test-Path $nuget)) {
    Write-Host "下载 nuget.exe ..."
    Invoke-WebRequest -Uri "https://dist.nuget.org/win-x86-commandline/latest/nuget.exe" `
        -OutFile $nuget -TimeoutSec 90
}

$nupkg = Join-Path $localNuget "LibreHardwareMonitorLib.$version.nupkg"
if (-not (Test-Path $nupkg)) {
    Write-Host "下载 LibreHardwareMonitorLib $version ..."
    Invoke-WebRequest -Uri "https://api.nuget.org/v3-flatcontainer/librehardwaremonitorlib/$version/librehardwaremonitorlib.$version.nupkg" `
        -OutFile $nupkg -TimeoutSec 180
}

Write-Host "解析依赖 ..."
& $nuget install LibreHardwareMonitorLib -Version $version -OutputDirectory $packages `
    -Source $localNuget -Source https://api.nuget.org/v3/index.json -NonInteractive |
    Select-Object -Last 3

# 摊平：pythonnet 的 CLR 程序集解析不会去翻 NuGet 的目录结构
foreach ($tfm in @(@{ dir = "flat472"; name = "net472" }, @{ dir = "flat8"; name = "net8.0" })) {
    $target = Join-Path $packages $tfm.dir
    Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $target | Out-Null
}

# LHM 本体必须取 runtimes 下带内嵌驱动的那份，ref 下的是引用程序集
Copy-Item (Join-Path $packages "LibreHardwareMonitorLib.$version\runtimes\win-x64\lib\net472\LibreHardwareMonitorLib.dll") `
    (Join-Path $packages "flat472") -Force
Copy-Item (Join-Path $packages "LibreHardwareMonitorLib.$version\runtimes\win-x64\lib\net8.0\LibreHardwareMonitorLib.dll") `
    (Join-Path $packages "flat8") -Force

foreach ($pkg in Get-ChildItem $packages -Directory | Where-Object { $_.Name -notmatch "^flat" }) {
    foreach ($tfm in @(@{ dir = "flat472"; name = "net472" }, @{ dir = "flat8"; name = "net8.0" })) {
        $dest = Join-Path $packages $tfm.dir
        foreach ($sub in @("lib\$($tfm.name)", "lib\netstandard2.0", "runtimes\win\lib\$($tfm.name)")) {
            $src = Join-Path $pkg.FullName $sub
            if (Test-Path $src) {
                Get-ChildItem $src -Filter *.dll -ErrorAction SilentlyContinue | ForEach-Object {
                    $target = Join-Path $dest $_.Name
                    if (-not (Test-Path $target)) { Copy-Item $_.FullName $target -Force }
                }
            }
        }
    }
}

foreach ($tfm in @("flat472", "flat8")) {
    $count = (Get-ChildItem (Join-Path $packages $tfm) -Filter *.dll).Count
    Write-Host "$tfm : $count 个 DLL"
}

Write-Host ""
Write-Host "完成。验证：" -ForegroundColor Green
Write-Host "  python -m vrmmon.doctor --provider lhm"

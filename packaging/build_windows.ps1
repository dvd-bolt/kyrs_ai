[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')]
    [string]$Version = "0.2.0",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$packagingDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = (Resolve-Path (Join-Path $packagingDir "..")).Path
$projectFile = Join-Path $repositoryRoot "pyproject.toml"
if (-not (Test-Path -LiteralPath $projectFile -PathType Leaf)) {
    throw "pyproject.toml was not found at $repositoryRoot"
}

$launcher = Get-Command py -ErrorAction SilentlyContinue
if (-not $launcher) {
    throw "Python Launcher (py.exe) is required. Install 64-bit Python 3.12 or 3.13 first."
}

$pythonSelector = $null
foreach ($candidate in @("-3.13", "-3.12")) {
    & $launcher.Source $candidate -c "import struct,sys; assert sys.version_info[:2] in {(3,12),(3,13)} and struct.calcsize('P') == 8"
    if ($LASTEXITCODE -eq 0) {
        $pythonSelector = $candidate
        break
    }
}
if (-not $pythonSelector) {
    throw "A supported 64-bit Python (3.12 or 3.13) is required for the Windows build."
}

$environmentDir = Join-Path $repositoryRoot ".venv-build-$($pythonSelector.TrimStart('-').Replace('.', ''))"
$python = Join-Path $environmentDir "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    & $launcher.Source $pythonSelector -m venv $environmentDir
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the build environment." }
}

& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to update pip." }
& $python -m pip install -e "${repositoryRoot}[desktop,visuals,build]"
if ($LASTEXITCODE -ne 0) { throw "Failed to install build dependencies." }

$distDir = Join-Path $repositoryRoot "dist"
$workDir = Join-Path $repositoryRoot "build\pyinstaller"
$specFile = Join-Path $packagingDir "papercraft.spec"
$previousBuildVersion = [Environment]::GetEnvironmentVariable("PAPERCRAFT_BUILD_VERSION", "Process")
try {
    [Environment]::SetEnvironmentVariable("PAPERCRAFT_BUILD_VERSION", $Version, "Process")
    & $python -m PyInstaller --noconfirm --clean --distpath $distDir --workpath $workDir $specFile
    $pyInstallerExitCode = $LASTEXITCODE
}
finally {
    [Environment]::SetEnvironmentVariable("PAPERCRAFT_BUILD_VERSION", $previousBuildVersion, "Process")
}
if ($pyInstallerExitCode -ne 0) { throw "PyInstaller build failed." }

$applicationDir = Join-Path $distDir "PaperCraftAI"
$applicationExe = Join-Path $applicationDir "PaperCraftAI.exe"
if (-not (Test-Path -LiteralPath $applicationExe -PathType Leaf)) {
    throw "The expected application executable was not created: $applicationExe"
}
Write-Host "Application assembled at $applicationDir" -ForegroundColor Green

if ($SkipInstaller) {
    Write-Host "Installer compilation skipped by request."
    exit 0
}

$isccCommand = Get-Command iscc -ErrorAction SilentlyContinue
$isccCandidates = @()
if ($isccCommand) { $isccCandidates += $isccCommand.Source }
if ($env:LOCALAPPDATA) {
    $isccCandidates += Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
}
if (${env:ProgramFiles(x86)}) {
    $isccCandidates += Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
}
if ($env:ProgramFiles) {
    $isccCandidates += Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"
}
$isccCandidates = $isccCandidates | Where-Object {
    $_ -and (Test-Path -LiteralPath $_ -PathType Leaf)
}
$iscc = $isccCandidates | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "Inno Setup 6 was not found. The onedir application is ready; no installer was compiled."
    exit 0
}

$installerOutput = Join-Path $distDir "installer"
New-Item -ItemType Directory -Path $installerOutput -Force | Out-Null
$installerScript = Join-Path $packagingDir "installer.iss"
& $iscc "/DAppVersion=$Version" "/DSourceDir=$applicationDir" "/DOutputDir=$installerOutput" $installerScript
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }
Write-Host "Installer assembled at $installerOutput" -ForegroundColor Green

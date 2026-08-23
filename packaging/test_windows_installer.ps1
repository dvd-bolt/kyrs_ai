[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Installer,
    [string]$PreviousInstaller = "",
    [string]$EvidenceRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$packagingDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = (Resolve-Path (Join-Path $packagingDir "..")).Path
$resolvedInstaller = (Resolve-Path -LiteralPath $Installer).Path
$resolvedPreviousInstaller = if ($PreviousInstaller) {
    (Resolve-Path -LiteralPath $PreviousInstaller).Path
} else {
    $resolvedInstaller
}
$resolvedEvidenceRoot = if ($EvidenceRoot) {
    [IO.Path]::GetFullPath($EvidenceRoot)
} else {
    Join-Path $repositoryRoot "build\stage3\installer-acceptance"
}
$caseRoot = Join-Path $resolvedEvidenceRoot ([DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss"))
$installDir = Join-Path $caseRoot "app"
$isolatedLocalAppData = Join-Path $caseRoot "localappdata"
$projectsRoot = Join-Path $isolatedLocalAppData "PaperCraftAI\projects"
New-Item -ItemType Directory -Path $projectsRoot -Force | Out-Null

function Invoke-Setup([string]$SetupPath) {
    $arguments = @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/DIR=`"$installDir`""
    )
    $process = Start-Process -FilePath $SetupPath -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "Installer failed with exit code $($process.ExitCode): $SetupPath"
    }
}

function Get-ProjectSnapshot {
    $records = Get-ChildItem -LiteralPath $projectsRoot -File -Recurse | Sort-Object FullName | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($projectsRoot.Length).TrimStart("\")
            length = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    }
    return ($records | ConvertTo-Json -Compress -Depth 4)
}

$fixtureCode = @'
import sys
from pathlib import Path
from papercraft.application import ProjectService
from papercraft.config import AppSettings
from papercraft.domain import ProjectBrief

root = Path(sys.argv[1]).resolve()
workspace = ProjectService(AppSettings(projects_root=root)).create(
    ProjectBrief(topic="Installer preservation fixture")
)
print(workspace.project.id)
'@
$fixtureScript = Join-Path $caseRoot "create_fixture.py"
[IO.File]::WriteAllText($fixtureScript, $fixtureCode, [Text.UTF8Encoding]::new($false))
$projectOutput = & uv run python $fixtureScript $projectsRoot
if ($LASTEXITCODE -ne 0) {
    throw "Could not run the project fixture creator"
}
$projectId = ($projectOutput | Select-Object -Last 1).Trim()
if (-not $projectId -or -not (Test-Path -LiteralPath (Join-Path $projectsRoot "$projectId\project.db"))) {
    throw "Could not create the isolated preservation fixture"
}
$sentinel = Join-Path $projectsRoot "$projectId\installer-preservation.txt"
[IO.File]::WriteAllText($sentinel, "PaperCraft installer preservation fixture`n")

$previousLocalAppData = [Environment]::GetEnvironmentVariable("LOCALAPPDATA", "Process")
try {
    [Environment]::SetEnvironmentVariable("LOCALAPPDATA", $isolatedLocalAppData, "Process")
    Invoke-Setup $resolvedPreviousInstaller

    $applicationExe = Join-Path $installDir "PaperCraftAI.exe"
    if (-not (Test-Path -LiteralPath $applicationExe -PathType Leaf)) {
        throw "Installed application executable is missing"
    }
    $applicationProcess = Start-Process -FilePath $applicationExe -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 5
    if ($applicationProcess.HasExited) {
        throw "Installed application exited during first-launch smoke"
    }
    Stop-Process -Id $applicationProcess.Id -Force
    Wait-Process -Id $applicationProcess.Id -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1

    $baseline = Get-ProjectSnapshot
    Invoke-Setup $resolvedInstaller
    $afterUpdate = Get-ProjectSnapshot
    if ($afterUpdate -cne $baseline) {
        throw "Project files changed during installer update"
    }

    $uninstaller = Join-Path $installDir "unins000.exe"
    if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        throw "Uninstaller is missing"
    }
    $uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
    ) -Wait -PassThru -WindowStyle Hidden
    if ($uninstallProcess.ExitCode -ne 0) {
        throw "Uninstaller failed with exit code $($uninstallProcess.ExitCode)"
    }
    if (Test-Path -LiteralPath $applicationExe -PathType Leaf) {
        throw "Application executable remains after uninstall"
    }
    $afterUninstall = Get-ProjectSnapshot
    if ($afterUninstall -cne $baseline) {
        throw "Project files changed during uninstall"
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $result = [ordered]@{
        status = "PASS"
        os = [Environment]::OSVersion.VersionString
        elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        project_id = $projectId
        project_file_count = @(Get-ChildItem -LiteralPath $projectsRoot -File -Recurse).Count
        previous_installer_sha256 = (Get-FileHash -LiteralPath $resolvedPreviousInstaller -Algorithm SHA256).Hash
        installer_sha256 = (Get-FileHash -LiteralPath $resolvedInstaller -Algorithm SHA256).Hash
        first_launch = "PASS"
        update_preservation = "PASS"
        uninstall_preservation = "PASS"
    }
    $evidencePath = Join-Path $caseRoot "acceptance.json"
    [IO.File]::WriteAllText(
        $evidencePath,
        (($result | ConvertTo-Json -Depth 4) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    $result | ConvertTo-Json -Depth 4
    Write-Host "Evidence: $evidencePath"
}
finally {
    [Environment]::SetEnvironmentVariable("LOCALAPPDATA", $previousLocalAppData, "Process")
}

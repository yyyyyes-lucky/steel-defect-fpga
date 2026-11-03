param(
    [string]$VivadoExe = '',
    [Alias('VitisHlsExe')][string]$VitisRunExe = '',
    [string]$ToolRoot = '',
    [string]$OutputRoot = '',
    [string]$ResumeFrom = '',
    [ValidateSet('Hls', 'ValidateBD', 'Full')][string]$Stage = 'Full',
    [switch]$CheckTools
)

$ErrorActionPreference = 'Stop'
# Resolve utility functions from this PowerShell installation, even when a
# parent application has mixed PowerShell 5/7 module directories in its env.
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -Force
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$expectedVersion = '2026.1'

function Find-Tool([string]$Provided, [string]$RelativePath, [string]$CommandName) {
    if ($Provided) {
        if (-not (Test-Path -LiteralPath $Provided -PathType Leaf)) { throw "Tool not found: $Provided" }
        return (Resolve-Path -LiteralPath $Provided).Path
    }
    $candidates = @()
    if ($ToolRoot) {
        $candidate = Join-Path $ToolRoot $RelativePath
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "ToolRoot does not contain $RelativePath : $ToolRoot" }
        return (Resolve-Path -LiteralPath $candidate).Path
    }
    foreach ($drive in Get-PSDrive -PSProvider FileSystem) {
        foreach ($vendor in @('AMDDesignTools', 'Xilinx')) {
            $candidate = Join-Path $drive.Root "$vendor/2026.1/$RelativePath"
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $candidates += (Resolve-Path -LiteralPath $candidate).Path }
        }
    }
    $candidates = @($candidates | Select-Object -Unique)
    if ($candidates.Count -eq 1) { return $candidates[0] }
    if ($candidates.Count -gt 1) { throw "Multiple 2026.1 installations found. Select -ToolRoot explicitly: $($candidates -join ', ')" }
    $fromPath = Get-Command $CommandName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($fromPath) { return $fromPath.Source }
    throw "Cannot find $CommandName. Supply -ToolRoot 'D:\AMDDesignTools\2026.1' using your actual installation folder, or explicit executable paths."
}

function Get-VersionText([string]$Executable, [string]$Argument) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = (& $Executable $Argument 2>&1 | Out-String)
    } finally { $ErrorActionPreference = $previous }
    # Some AMD launchers return a nonzero status for version-only requests.
    if ($output -notmatch '(?i)\b(?:vitis-run|vivado)\s+v?2026\.1\b') {
        throw "Expected $Executable version 2026.1. Actual output: $output"
    }
    return $output.Trim()
}

function Version-Identity([string]$Text) {
    # Ignore copyright and the session start timestamp; retain release/build identities.
    return (($Text -split "`r?`n" | Where-Object { $_ -match '(?i)(vitis-run v|^vivado v|SW Build|IP Build|SharedData Build|Tool Version Limit)' } |
        ForEach-Object { $_.Trim() }) -join "`n")
}

$VivadoExe = Find-Tool $VivadoExe 'Vivado/bin/vivado.bat' 'vivado.bat'
$VitisRunExe = Find-Tool $VitisRunExe 'Vitis/bin/vitis-run.bat' 'vitis-run.bat'
if ([IO.Path]::GetFileNameWithoutExtension($VitisRunExe) -ne 'vitis-run') {
    throw '2026.1 HLS requires vitis-run.bat --mode hls --tcl. Do not pass vitis_hls.bat or vitis.bat.'
}
$vivadoVersion = Get-VersionText $VivadoExe '-version'
$hlsVersion = Get-VersionText $VitisRunExe '--version'
$hlsFingerprintVersion = $hlsVersion
Write-Host "VIVADO_EXE=$VivadoExe"
Write-Host "VITIS_RUN_EXE=$VitisRunExe"
Write-Host $vivadoVersion
Write-Host $hlsVersion
Write-Host 'TOOLS_2026_1_PASS=True'
if ($CheckTools) { return }

if ($ResumeFrom) {
    $work = (Resolve-Path -LiteralPath $ResumeFrom).Path
    if (-not (Test-Path -LiteralPath (Join-Path $work 'build_state.json'))) { throw 'ResumeFrom is not a build directory created by this revised script.' }
    $state = Get-Content -LiteralPath (Join-Path $work 'build_state.json') -Raw | ConvertFrom-Json
    if ($state.schema -ne 'steel-build-2026.1/2' -or $state.repo -ne $repo -or
        (Version-Identity $state.vivado_version) -ne (Version-Identity $vivadoVersion) -or
        (Version-Identity $state.hls_version) -ne (Version-Identity $hlsVersion)) {
        throw 'Resume source directory or tool versions differ. Use a fresh build directory.'
    }
    $hlsFingerprintVersion = $state.hls_version
} else {
    if (-not $OutputRoot) { $OutputRoot = Join-Path $repo 'build/output' }
    $OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
    $work = Join-Path $OutputRoot ('v10_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
    New-Item -ItemType Directory -Path $work -ErrorAction Stop | Out-Null
    @{schema='steel-build-2026.1/2'; repo=$repo; vivado_version=$vivadoVersion; hls_version=$hlsVersion} |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $work 'build_state.json') -Encoding UTF8
}
New-Item -ItemType Directory -Path (Join-Path $work 'logs') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $work 'receipts') -Force | Out-Null
Write-Host "BUILD_DIRECTORY=$work"

function Invoke-Tool([string]$Executable, [string[]]$Arguments, [string]$Label, [string]$Log) {
    Write-Host "RUN=$Label LOG=$Log"
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @Arguments *> $Log
        $toolExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    if ($toolExit -ne 0) {
        Get-Content -LiteralPath $Log -Tail 35 | Write-Host
        throw "$Label failed (exit $toolExit). Preserve $work. Read $Log before retrying."
    }
}

function Fingerprint([System.Collections.IDictionary]$Inputs) {
    $payload = ($Inputs.Keys | Sort-Object | ForEach-Object { "$_=$($Inputs[$_])" }) -join "`n"
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($payload)))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

$inputHashes = @{}
foreach ($core in @('gaussian_row_q30', 'ellipse7', 'blur3')) {
    $dst = Join-Path $work "hls/$core"
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    foreach ($area in @('src', 'sim', 'build')) {
        foreach ($source in Get-ChildItem -LiteralPath (Join-Path $repo "$area/hls/$core") -File) {
            $key = "$core/$area/$($source.Name)"
            $inputHashes[$key] = (Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash
            $destination = Join-Path $dst $source.Name
            if ((Test-Path -LiteralPath $destination) -and (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $inputHashes[$key]) {
                throw "Staged source changed: $destination. Use a fresh build directory."
            }
            if (-not (Test-Path -LiteralPath $destination)) { Copy-Item -LiteralPath $source.FullName -Destination $destination }
        }
    }
}

function Run-HlsStage([string]$Name, [string]$Core, [string]$Tcl, [string]$Project, [string]$Vlnv) {
    $stageInputs = @{}
    foreach ($key in $inputHashes.Keys) { if ($key.StartsWith("$Core/")) { $stageInputs[$key] = $inputHashes[$key] } }
    $stageInputs['tool'] = $hlsFingerprintVersion
    $fingerprint = Fingerprint $stageInputs
    $receiptPath = Join-Path $work "receipts/$Name.json"
    $projectPath = Join-Path $work "hls/$Core/$Project"
    $log = Join-Path $work "logs/$Name.log"
    if (Test-Path -LiteralPath $receiptPath) {
        $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
        if ($receipt.input_sha256 -ne $fingerprint) { throw "Inputs changed for $Name. Use a fresh build directory." }
        foreach ($entry in $receipt.outputs.PSObject.Properties) {
            $path = Join-Path $work $entry.Name
            if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.Value) {
                throw "Saved HLS output missing or changed: $path. Use a fresh build directory."
            }
        }
        Write-Host "REUSE_VERIFIED_STAGE=$Name"
        return
    }
    if (Test-Path -LiteralPath $projectPath) { throw "Unfinished HLS project exists: $projectPath. Preserve its log and start in a fresh output directory." }
    $tclPath = Join-Path $work "hls/$Core/$Tcl"
    Invoke-Tool $VitisRunExe @('--mode', 'hls', '--tcl', $tclPath) $Name $log
    if (-not (Select-String -LiteralPath $log -SimpleMatch "STEEL_STAGE_PASS=$Name" -Quiet)) {
        throw "HLS returned without its success marker: $Name. Inspect $log. Vivado has not started."
    }
    if ($Vlnv) {
        $xmlPath = Join-Path $projectPath 'solution1/impl/ip/component.xml'
        if (-not (Test-Path -LiteralPath $xmlPath -PathType Leaf)) { throw "HLS export missing: $xmlPath. Inspect $log. Vivado has not started." }
        [xml]$xml = Get-Content -LiteralPath $xmlPath -Raw
        $parts = @('vendor', 'library', 'name', 'version') | ForEach-Object { $xml.DocumentElement.SelectSingleNode("*[local-name()='$_']").InnerText }
        if (($parts -join ':') -ne $Vlnv) { throw "Unexpected IP VLNV in $xmlPath : $($parts -join ':')" }
    }
    $outputs = @{}
    foreach ($file in Get-ChildItem -LiteralPath $projectPath -Recurse -File) {
        $rel = $file.FullName.Substring($work.Length + 1).Replace('\', '/')
        $outputs[$rel] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
    }
    if ($outputs.Count -eq 0) { throw "No outputs generated for $Name" }
    @{stage=$Name; input_sha256=$fingerprint; outputs=$outputs; checked_at=(Get-Date).ToString('o')} |
        ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
    Write-Host "HLS_STAGE_PASS=$Name"
}

Push-Location $work
$savedRoot = $env:STEEL_REPRO_ROOT
$savedWork = $env:STEEL_REPRO_WORK
$savedStage = $env:STEEL_REPRO_STAGE
try {
    Run-HlsStage 'gaussian' 'gaussian_row_q30' 'run_hls.tcl' 'continuous_project' 'steel.local:hls:gaussian_row_q30:2.1'
    Run-HlsStage 'gaussian_extended' 'gaussian_row_q30' 'run_extended.tcl' 'extended_project' ''
    Run-HlsStage 'ellipse7' 'ellipse7' 'run_hls_export.tcl' 'ellipse7_project' 'steel.local:hls:ellipse7:1.0'
    Run-HlsStage 'blur3' 'blur3' 'run_hls.tcl' 'blur3_project' 'steel.local:hls:blur3:1.0'
    Write-Host 'ALL_HLS_IPS_READY=True'
    if ($Stage -eq 'Hls') { Write-Host 'HLS_ONLY_PASS=True'; return }
    if (Test-Path -LiteralPath (Join-Path $work 'vivado')) { throw 'A Vivado project already exists here. Preserve it; automatic reuse is limited to verified HLS stages.' }
    $env:STEEL_REPRO_ROOT = $repo
    $env:STEEL_REPRO_WORK = $work
    $env:STEEL_REPRO_STAGE = $Stage
    $vivadoLog = Join-Path $work 'logs/vivado_console.log'
    Invoke-Tool $VivadoExe @('-mode', 'batch', '-source', (Join-Path $repo 'build/build_vivado.tcl'),
        '-log', (Join-Path $work 'vivado_build.log'), '-journal', (Join-Path $work 'vivado_build.jou')) 'Vivado system build' $vivadoLog
    if ($Stage -eq 'ValidateBD') {
        if (-not (Select-String -LiteralPath $vivadoLog -SimpleMatch 'GAUSSIAN_V10_BD_VALIDATION_PASS' -Quiet)) { throw 'BD validation marker missing' }
        Write-Host 'BD_ONLY_PASS=True'; return
    }
    if (-not (Select-String -LiteralPath $vivadoLog -SimpleMatch 'GAUSSIAN_V10_BUILD_AND_EXPORT_PASS' -Quiet)) { throw 'Vivado export success marker missing' }
    $bit = Join-Path $work 'hardware/steel_gaussian_v10.bit'
    $hwh = Join-Path $work 'hardware/steel_gaussian_v10.hwh'
    foreach ($path in @($bit, $hwh)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -eq 0) { throw "Missing or empty output: $path" }
    }
    $expected = Get-Content -LiteralPath (Join-Path $repo 'build/reference/build_evidence.json') -Raw | ConvertFrom-Json
    $bitHash = (Get-FileHash -LiteralPath $bit -Algorithm SHA256).Hash.ToLowerInvariant()
    $hwhHash = (Get-FileHash -LiteralPath $hwh -Algorithm SHA256).Hash.ToLowerInvariant()
    $summary = [ordered]@{
        completed=$true; target_part='xc7z020clg400-1'; expected_vivado_version='2026.1'
        vivado_version=$vivadoVersion; hls_version=$hlsVersion; hls_command='vitis-run --mode hls --tcl'
        bit_sha256=$bitHash; hwh_sha256=$hwhHash
        reference_bit_sha256=$expected.bit_sha256; reference_hwh_sha256=$expected.hwh_sha256
        bit_byte_identical_to_reference=($bitHash -eq $expected.bit_sha256)
        hwh_byte_identical_to_reference=($hwhHash -eq $expected.hwh_sha256)
        build_directory=$work; board_tested_by_this_script=$false
        build_script_sha256=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $summaryPath = Join-Path $work 'build_summary.json'
    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    Write-Host "BIT_FILE=$bit"
    Write-Host "HWH_FILE=$hwh"
    Write-Host "SUMMARY_FILE=$summaryPath"
    Write-Host "BIT_BYTE_IDENTICAL=$($summary.bit_byte_identical_to_reference)"
    Write-Host "HWH_BYTE_IDENTICAL=$($summary.hwh_byte_identical_to_reference)"
    Write-Host 'BUILD_AND_EXPORT_PASS=True'
} finally {
    $env:STEEL_REPRO_ROOT = $savedRoot
    $env:STEEL_REPRO_WORK = $savedWork
    $env:STEEL_REPRO_STAGE = $savedStage
    Pop-Location
}

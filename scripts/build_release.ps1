param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$pluginRoot = Split-Path -Parent $PSScriptRoot
$parent = Split-Path -Parent $pluginRoot
$pluginName = Split-Path -Leaf $pluginRoot

if (-not $Version) {
    $metadata = Get-Content -Raw (Join-Path $pluginRoot "metadata.yaml")
    if ($metadata -notmatch 'version:\s*"([^"]+)"') {
        throw "Cannot read plugin version from metadata.yaml"
    }
    $Version = $Matches[1]
}

$zip = Join-Path $parent "$pluginName`_v$Version.zip"
$excludes = @(
    "$pluginName/.git",
    "$pluginName/__pycache__",
    "$pluginName/.pytest_cache",
    "$pluginName/.ruff_cache",
    "$pluginName/data",
    "$pluginName/docs/images/preview1.png",
    "$pluginName/docs/images/preview2.png",
    "$pluginName/docs/images/preview3.png",
    "$pluginName/docs/images/preview4.png",
    "$pluginName/tests/__pycache__"
)

Push-Location $parent
try {
    $args = @("-a", "-c", "-f", $zip)
    foreach ($pattern in $excludes) {
        $args += "--exclude=$pattern"
    }
    $args += $pluginName
    & tar @args
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

Write-Output $zip

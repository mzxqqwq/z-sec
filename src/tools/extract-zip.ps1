# extract-zip2.ps1 <zipPath> <destDir> - entry-by-entry extraction with Windows-invalid-name sanitization
param(
    [Parameter(Mandatory=$true)][string]$ZipPath,
    [Parameter(Mandatory=$true)][string]$DestDir
)
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
$count = 0
$skipped = 0
$renamed = @()
$reserved = @('CON','PRN','AUX','NUL','COM1','COM2','COM3','COM4','COM5','COM6','COM7','COM8','COM9','LPT1','LPT2','LPT3','LPT4','LPT5','LPT6','LPT7','LPT8','LPT9')
foreach ($entry in $zip.Entries) {
    $parts = $entry.FullName -split '/'
    if ($parts.Count -lt 2) { continue }          # strip zip top-level dir name
    $rel = ($parts[1..($parts.Count-1)] -join '/')
    if ([string]::IsNullOrEmpty($rel)) { continue }
    # sanitize per segment: invalid chars (? -> _q aligned with PATH_OVERRIDES, others -> _),
    # trailing dots/spaces (silently stripped by Windows), reserved names
    $segs = $rel -split '/'
    $bad = [char[]]('*',':','<','>','|','"')
    for ($i = 0; $i -lt $segs.Count; $i++) {
        $s = $segs[$i]
        $orig = $s
        $s = $s.Replace('?', '_q')
        foreach ($b in $bad) { $s = $s.Replace([string]$b, '_') }
        $s = $s.TrimEnd('.', ' ')
        $stem = ($s -split '\.')[0]
        if ($reserved -contains $stem) { $s = '_' + $s }
        if (-not $s) { $s = '_' }
        if ($s -ne $orig) { $renamed += "$($entry.FullName) -> $s" }
        $segs[$i] = $s
    }
    $rel = $segs -join '\'
    $target = Join-Path $DestDir $rel
    if ($entry.FullName.EndsWith('/')) {
        if (-not (Test-Path $target)) { New-Item -ItemType Directory -Force -Path $target | Out-Null }
    } else {
        if (Test-Path $target) { $skipped++; $count++; continue }   # idempotent: skip existing
        $dir = Split-Path $target -Parent
        if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        $in = $entry.Open()
        $out = [System.IO.File]::Create($target)
        $in.CopyTo($out)
        $out.Close(); $in.Close()
    }
    $count++
    if ($count % 2000 -eq 0) { Write-Host "  ... $count entries (skipped $skipped)" }
}
$zip.Dispose()
Write-Host "processed $count entries (skipped existing: $skipped) to $DestDir"
if ($renamed.Count -gt 0) { Write-Host "renamed:"; $renamed | Select-Object -First 15 | ForEach-Object { Write-Host "  $_" } }

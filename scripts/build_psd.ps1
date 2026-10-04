param([Parameter(Mandatory=$true)][string]$Spec, [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$taskConfig = Get-Content -LiteralPath (Resolve-Path -LiteralPath $Spec).Path -Raw -Encoding UTF8 | ConvertFrom-Json
& $Python -B -X utf8 (Join-Path $PSScriptRoot 'preflight.py') $Spec
if ($LASTEXITCODE -ne 0) { throw 'Asset preflight failed; Photoshop was not invoked.' }
$taskOutputPaths = @($taskConfig.outputPsd, $taskConfig.outputJpg, $taskConfig.qaPng)
$taskOutputExtensions = @('.psd', '.jpg', '.png')
foreach ($taskView in $taskConfig.qaViews) { $taskOutputPaths += $taskView.path; $taskOutputExtensions += '.png' }
function Get-TaskExports($groups) {
    foreach ($taskGroup in $groups) {
        foreach ($taskLayer in $taskGroup.layers) { if ($taskLayer.exportPng) { $taskLayer.exportPng } }
        Get-TaskExports $taskGroup.groups
    }
}
foreach ($taskExport in (Get-TaskExports $taskConfig.groups)) { $taskOutputPaths += $taskExport; $taskOutputExtensions += '.png' }
if (($taskOutputPaths | Select-Object -Unique).Count -ne $taskOutputPaths.Count) { throw 'Duplicate output paths.' }
for ($taskIndex = 0; $taskIndex -lt $taskOutputPaths.Count; $taskIndex++) {
    $taskPath = [string]$taskOutputPaths[$taskIndex]
    if (-not [IO.Path]::IsPathRooted($taskPath)) { throw 'Outputs require absolute paths.' }
    if ([IO.Path]::GetExtension($taskPath) -ne $taskOutputExtensions[$taskIndex]) { throw "Wrong output extension: $taskPath" }
    if (Test-Path -LiteralPath $taskPath) { throw "Output already exists; choose a new version: $taskPath" }
}
if ($taskConfig.width -le 0 -or $taskConfig.height -le 0 -or [Math]::Max($taskConfig.width,$taskConfig.height) -gt 4096 -or -not $taskConfig.groups) { throw 'Valid canvas (max 4096) and groups required.' }
$taskNames = New-Object 'System.Collections.Generic.HashSet[string]'
function Test-TaskGroups($groups) {
    foreach ($taskGroup in $groups) {
        if (-not $taskNames.Add([string]$taskGroup.name)) { throw 'Use globally unique layer/group names.' }
        foreach ($taskLayer in $taskGroup.layers) {
            if (-not $taskNames.Add([string]$taskLayer.name)) { throw 'Use globally unique layer/group names.' }
            if ($taskLayer.maskFile -or $taskLayer.maskPolygon) { throw 'Original-photo cutout masks are not supported by this reconstruction composer.' }
            if ($taskLayer.type -notin @('image','text','fill')) { throw 'Unknown layer type.' }
            if ($taskLayer.type -eq 'image') {
                $taskPath = [string]$taskLayer.path
                if (-not [IO.Path]::IsPathRooted($taskPath) -or -not (Test-Path -LiteralPath $taskPath -PathType Leaf)) { throw "Missing absolute asset path: $taskPath" }
            }
        }
        Test-TaskGroups $taskGroup.groups
    }
}
Test-TaskGroups $taskConfig.groups
foreach ($taskPath in $taskOutputPaths) { New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($taskPath)) | Out-Null }
$taskJson = $taskConfig | ConvertTo-Json -Depth 60 -Compress
$taskJson = $taskJson.Replace([string][char]0x2028, '\u2028').Replace([string][char]0x2029, '\u2029')
$taskBody = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'compose.jsx') -Raw -Encoding UTF8
$taskApp = New-Object -ComObject Photoshop.Application
$taskApp.DoJavaScript("var spec = $taskJson;`n$taskBody")

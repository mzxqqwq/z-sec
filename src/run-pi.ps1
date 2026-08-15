# run-pi.ps1 - pi launcher: loads DeepSeek key from local secrets, adds Kali extension
# Usage:
#   .\run-pi.ps1 --list-models
#   .\run-pi.ps1 -p "your prompt"      # print mode
#   .\run-pi.ps1                       # interactive TUI
# Prereqs:
#   D:\ctf-agent\pi-mono built (npm run build:offline)
#   D:\ctf-agent\secrets\deepseek.key contains the API key
#   Kali API (default http://10.174.153.128:5000) online
#
# WARNING: PowerShell 5.1 forwards args containing double quotes to node
# by SPLITTING them into multiple argv (prompt 里的 "FLAG: ..."、题目 JSON、
# 计划 JSON 都会被拆). Any fragment starting with "-" then kills pi with
# "Unknown option: --". The orchestrator therefore calls node directly
# (see ctf_orchestrator.DEFAULT_PI_CMD + workers._worker_env).
# Only use this script manually with prompts that contain NO double quotes.
$ErrorActionPreference = 'Stop'

$Root = 'D:\ctf-agent'
$PiCli = Join-Path $Root 'pi-mono\packages\coding-agent\dist\cli.js'
$KeyFile = Join-Path $Root 'secrets\deepseek.key'
$KaliExt = Join-Path $Root 'src\pi-ext\kali.ts'

if (-not (Test-Path $PiCli)) { throw "pi CLI not built: $PiCli" }
if (-not (Test-Path $KeyFile)) { throw "missing $KeyFile (put your DeepSeek API key there, one line)" }

$env:DEEPSEEK_API_KEY = (Get-Content $KeyFile -Raw).Trim()
$env:PI_CODING_AGENT_DIR = "$env:USERPROFILE\.pi\agent"
if (-not $env:KALI_API_URL) { $env:KALI_API_URL = "http://10.174.153.128:5000" }

# Default: builtin deepseek provider + kali extension. Model: deepseek-v4-flash
# unless the caller passes --model (v4-pro for hard challenges).
$defaults = @("--provider", "deepseek", "-e", $KaliExt)
$hasModel = $false
for ($i = 0; $i -lt $args.Count; $i++) { if ($args[$i] -eq "--model") { $hasModel = $true; break } }
if (-not $hasModel) { $defaults += @("--model", "deepseek-v4-flash") }
$hasThinking = $false
for ($i = 0; $i -lt $args.Count; $i++) { if ($args[$i] -eq "--thinking") { $hasThinking = $true; break } }
if (-not $hasThinking) { $defaults += @("--thinking", "low") }
node $PiCli @defaults @args
exit $LASTEXITCODE

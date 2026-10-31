# PowerShell wrapper for Kavach tasks
# Usage:
#   .\run.ps1 test
#   .\run.ps1 demo -Fast
#   .\run.ps1 demo -Video
#   .\run.ps1 eval

param (
    [Parameter(Position=0, Mandatory=$true)]
    [string]$Command,

    [switch]$Fast,
    [switch]$Video,
    [int]$Trials = 20,
    [switch]$Live
)

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$argsList = @("tasks.py", $Command)

if ($Command -eq "demo") {
    if ($Fast) { $argsList += "--fast" }
    if ($Video) { $argsList += "--video" }
}

if ($Command -eq "eval") {
    $argsList += "--trials"
    $argsList += $Trials.ToString()
    if ($Live) { $argsList += "--live" }
}

python $argsList

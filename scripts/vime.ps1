# VIME launcher for PowerShell (Windows / cross-platform).
# Delegates to the shared Python launcher.
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$python = if ($env:VIME_PYTHON) { $env:VIME_PYTHON } else { "python" }
& $python "$scriptDir\vime_launcher.py" @args
exit $LASTEXITCODE

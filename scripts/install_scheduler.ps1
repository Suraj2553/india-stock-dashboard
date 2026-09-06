# install_scheduler.ps1 — register twice-daily Market Monitor scans in Windows Task Scheduler.
#
#   Right-click → "Run with PowerShell", or from a terminal:
#     powershell -ExecutionPolicy Bypass -File scripts\install_scheduler.ps1
#
# Creates two tasks (08:45 and 15:45 IST = local time of this PC — adjust below if your
# PC clock is not on IST). "StartWhenAvailable" makes a missed run start as soon as the
# laptop is next switched on, so you still get both e-mails on days you boot late.

$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$script = Join-Path $root "scripts\daily_scan.py"

$times = @("08:45", "15:45")
foreach ($t in $times) {
    $name    = "MarketMonitor Scan $t"
    $action  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`" --slot $t" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Daily -At $t
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
    try { Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue } catch {}
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
        -Description "Market Monitor: scan the market and e-mail buy ideas" | Out-Null
    Write-Host "[OK] Registered task '$name' -> $python $script --slot $t"
}
Write-Host ""
Write-Host "Done. Tasks run daily at $($times -join ' and ') (local time) and catch up after a late boot."
Write-Host "Configure SMTP + recipient in the dashboard (Settings -> E-mail alerts) or in .env first."
Write-Host "Remove with: scripts\uninstall_scheduler.ps1"

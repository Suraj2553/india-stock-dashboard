# uninstall_scheduler.ps1 — remove the Market Monitor scheduled tasks
foreach ($t in @("08:45", "15:45")) {
    $name = "MarketMonitor Scan " + ($t -replace ":", "")
    try {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
        Write-Host "[OK] Removed '$name'"
    } catch {
        Write-Host "[--] '$name' was not registered"
    }
}

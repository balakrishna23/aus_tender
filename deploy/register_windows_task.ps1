# Runs the scrapers every 6 hours on this Windows PC (until the VPS takes over).
# Run once from PowerShell in the project folder:
#   powershell -ExecutionPolicy Bypass -File deploy\register_windows_task.ps1
# Remove with: Unregister-ScheduledTask -TaskName AusTenderScrapers -Confirm:$false
$project = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python).Source
$command = "Set-Location '$project'; & '$python' run_spiders.py --timeout 14400 --retries 1; & '$python' backup_databases.py --keep 14"

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$command`"" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(6).AddMinutes(15) `
    -RepetitionInterval (New-TimeSpan -Hours 6)
# StartWhenAvailable: run a missed slot as soon as the PC is back on.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 12) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName 'AusTenderScrapers' -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Incremental AusTender/BuyICT/ACT/NSW scrape every 6 hours' -Force
Get-ScheduledTask -TaskName 'AusTenderScrapers' | Get-ScheduledTaskInfo | Select-Object NextRunTime

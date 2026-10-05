# Runs the scrapers every 6 hours on this Windows PC (until the VPS takes over).
# Run once from PowerShell in the project folder:
#   powershell -ExecutionPolicy Bypass -File deploy\register_windows_task.ps1
# Also registers the outreach agent hourly (it sends only 9am-4pm Sydney time on business days).
# Remove with: Unregister-ScheduledTask -TaskName AusTenderScrapers,AusTenderOutreachAgent -Confirm:$false
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

# Outreach agent: every hour. It checks the inbox each time, sends only inside the Sydney
# sending window, and emails the daily summary after the window closes on business days.
$agentCommand = "Set-Location '$project'; & '$python' outreach_agent.py scheduled *>> logs\outreach_agent.log"
$agentAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$agentCommand`"" -WorkingDirectory $project
$agentTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours((Get-Date).Hour + 1).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Hours 1)
$agentSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 50) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'AusTenderOutreachAgent' -Action $agentAction -Trigger $agentTrigger `
    -Settings $agentSettings -Description 'Tender outreach agent (see OUTREACH_MODE in .env)' -Force
Get-ScheduledTask -TaskName 'AusTenderOutreachAgent' | Get-ScheduledTaskInfo | Select-Object NextRunTime

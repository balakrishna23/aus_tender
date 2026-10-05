# Runs the scrapers every 6 hours on this Windows PC (until the VPS takes over).
# Run once from PowerShell in the project folder:
#   powershell -ExecutionPolicy Bypass -File deploy\register_windows_task.ps1
# Also registers the outreach agent every 15 minutes (it sends only 9am-4pm Sydney time on business days).
# Remove with: Unregister-ScheduledTask -TaskName AusTenderScrapers,AusTenderOutreachAgent,AusTenderBuyIctHourly -Confirm:$false
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

# Outreach agent: every 15 minutes. It checks the inbox each time, sends only inside the Sydney
# sending window, and emails the daily summary after the window closes on business days.
# Through cmd.exe: Windows PowerShell 5.1 reports a program's log lines on stderr as a failure
# (exit code 1, NativeCommandError noise in the log); cmd appends them as plain text.
$agentAction = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument "/c `"`"$python`" outreach_agent.py scheduled >> logs\outreach_agent.log 2>&1`"" -WorkingDirectory $project
$agentTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours((Get-Date).Hour + 1).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Minutes 15)
$agentSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 14) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'AusTenderOutreachAgent' -Action $agentAction -Trigger $agentTrigger `
    -Settings $agentSettings -Description 'Tender outreach agent (see OUTREACH_MODE in .env)' -Force
Get-ScheduledTask -TaskName 'AusTenderOutreachAgent' | Get-ScheduledTaskInfo | Select-Object NextRunTime

# BuyICT (where most people-related tenders appear) every hour from 03:35 to 11:35 on this PC,
# which covers 9am-4pm Sydney time in both daylight-saving and standard time. Feed only: the
# 6-hourly full run refreshes the Google Sheets. At :35 so it never collides with the full run.
$buyictAction = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument "/c `"`"$python`" run_spiders.py --spiders buy_ict --no-sync --retries 1 >> logs\buyict_hourly.log 2>&1`"" `
    -WorkingDirectory $project
$buyictTrigger = New-ScheduledTaskTrigger -Daily -At '03:35'
$buyictTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At '03:35' -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Hours 8)).Repetition
$buyictSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'AusTenderBuyIctHourly' -Action $buyictAction -Trigger $buyictTrigger `
    -Settings $buyictSettings -Description 'BuyICT check every hour in Sydney business hours (feed only)' -Force
Get-ScheduledTask -TaskName 'AusTenderBuyIctHourly' | Get-ScheduledTaskInfo | Select-Object NextRunTime

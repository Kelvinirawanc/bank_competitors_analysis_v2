$ErrorActionPreference = "Stop"

$TaskName = "Allo Bank Competitive Intelligence - Google Play Daily"
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunBat = Join-Path $Project "run_daily_scraper.bat"

Write-Host "Creating scheduled task:"
Write-Host "  $TaskName"
Write-Host "Target:"
Write-Host "  00:00 Asia/Jakarta (GMT+7)"
Write-Host ""

$tz = Get-TimeZone
Write-Host "Current Windows timezone: $($tz.Id) / $($tz.DisplayName)"

if ($tz.Id -notmatch "SE Asia Standard Time|Singapore Standard Time|W. Indonesia Standard Time") {
    Write-Warning "Your Windows timezone does not appear to be GMT+7."
    Write-Warning "Task Scheduler uses local time. Set Windows timezone to"
    Write-Warning "'(UTC+07:00) Bangkok, Hanoi, Jakarta' if you want 00:00 GMT+7."
}

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$RunBat`"" `
    -WorkingDirectory $Project

$trigger = New-ScheduledTaskTrigger -Daily -At 00:00

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType InteractiveToken `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host ""
Write-Host "SUCCESS: task installed."
Write-Host "Next run:"
Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo |
    Select-Object TaskName, LastRunTime, NextRunTime, LastTaskResult |
    Format-List

Write-Host "To remove it later:"
Write-Host "schtasks /Delete /TN `"$TaskName`" /F"

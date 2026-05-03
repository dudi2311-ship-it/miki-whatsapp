# Reads today's Outlook calendar items and POSTs them to miki's /sync/iphone-events endpoint.
# Run on the work laptop each morning (or via Task Scheduler).
#
# Requirements: Windows + Outlook Desktop (any modern version), PowerShell 5+.
# No installation needed.

$ErrorActionPreference = "Stop"

$endpoint = "https://miki-whatsapp.onrender.com/sync/iphone-events"
$token    = "FrPN1qRhBWos0f9cibG54KzSVp7fsh1aLmZWmsGXaC8"

Write-Host "Connecting to Outlook..." -ForegroundColor Cyan
$outlook = New-Object -ComObject Outlook.Application
$ns      = $outlook.GetNamespace("MAPI")
$cal     = $ns.GetDefaultFolder(9)   # 9 = olFolderCalendar

$items = $cal.Items
$items.IncludeRecurrences = $true
$items.Sort("[Start]")

$startOfDay = (Get-Date).Date
$endOfDay   = $startOfDay.AddDays(1)

# Outlook restriction format requires "MM/dd/yyyy hh:mm tt" (US locale)
$filterStart = $startOfDay.ToString("MM/dd/yyyy hh:mm tt", [Globalization.CultureInfo]::InvariantCulture)
$filterEnd   = $endOfDay.ToString("MM/dd/yyyy hh:mm tt",   [Globalization.CultureInfo]::InvariantCulture)
$filter      = "[Start] < '$filterEnd' AND [End] > '$filterStart'"

$todayItems = $items.Restrict($filter)

$events = @()
foreach ($item in $todayItems) {
    $events += [ordered]@{
        title         = if ($item.Subject)  { $item.Subject }  else { "(ללא כותרת)" }
        start_iso     = $item.Start.ToString("yyyy-MM-ddTHH:mm:sszzz")
        end_iso       = $item.End.ToString("yyyy-MM-ddTHH:mm:sszzz")
        location      = if ($item.Location) { $item.Location } else { "" }
        notes         = ""
        calendar_name = "Outlook"
    }
}

Write-Host ("Found {0} events for today" -f $events.Count) -ForegroundColor Green

$payload = @{ events = $events } | ConvertTo-Json -Depth 5
$bytes   = [System.Text.Encoding]::UTF8.GetBytes($payload)

Write-Host "Sending to miki..." -ForegroundColor Cyan
$response = Invoke-RestMethod -Uri $endpoint `
    -Method POST `
    -Headers @{ "X-Cron-Token" = $token } `
    -ContentType "application/json; charset=utf-8" `
    -Body $bytes

Write-Host ("Done. Server stored {0} events." -f $response.stored) -ForegroundColor Green

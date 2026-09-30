# PLAG — put your API keys on this laptop, once.
#
#   Right-click this file -> "Run with PowerShell"   (or:  powershell -ExecutionPolicy Bypass -File setup-keys.ps1)
#
# It writes %LOCALAPPDATA%\PLAG\keys.json, which PLAG loads into Windows Credential Manager every time it starts.
# That file stays on this laptop: it is not in the repo and git ignores it, so no key ever reaches GitHub.
# Leave a key blank to skip it. Run it again any time to change one.

$ErrorActionPreference = 'Stop'
$dir  = Join-Path $env:LOCALAPPDATA 'PLAG'
$file = Join-Path $dir 'keys.json'

$fields = [ordered]@{
  'gemini'         = 'Gemini (the main brain)          aistudio.google.com/apikey'
  'groq'           = 'Groq (fast brain + hearing)      console.groq.com/keys'
  'nvidia_glm'     = 'NVIDIA GLM 5.3 Flash             build.nvidia.com'
  'nvidia_muse'    = 'NVIDIA Muse                      build.nvidia.com'
  'nvidia_hearing' = 'NVIDIA Parakeet (hears you)      build.nvidia.com'
  'nvidia_speech'  = 'NVIDIA Leo (PLAG''s voice)        build.nvidia.com'
  'nvidia'         = 'NVIDIA (everything else)         build.nvidia.com'
  'elevenlabs'     = 'ElevenLabs voice   (optional)    elevenlabs.io'
  'fishaudio'      = 'Fish Audio voice   (optional)    fish.audio'
  'tavily'         = 'Tavily web search  (optional)    app.tavily.com'
  'tinyfish'       = 'TinyFish web agent (optional)    tinyfish.ai'
  'calcom'         = 'Cal.com calendar   (optional)    cal.com -> Settings -> Security'
  'github'         = 'GitHub token (CodeRabbit agent) github.com -> Settings -> Developer settings'
  'email'          = 'Your email address (Inbox agent)  e.g. you@gmail.com'
  'mail_password'  = 'That mailbox app password        myaccount.google.com/apppasswords'
}

$existing = @{}
if (Test-Path $file) {
  try { (Get-Content $file -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $existing[$_.Name] = $_.Value } }
  catch { Write-Host 'The existing keys.json could not be read; starting fresh.' -ForegroundColor Yellow }
}

Write-Host ''
Write-Host '  PLAG keys' -ForegroundColor Cyan
Write-Host '  Paste a key and press Enter. Press Enter on its own to keep or skip one.' -ForegroundColor DarkGray
Write-Host ''

$keys = [ordered]@{}
foreach ($name in $fields.Keys) {
  $have = $existing[$name]
  $mark = if ($have) { ' [saved]' } else { '' }
  Write-Host ("  {0}{1}" -f $fields[$name], $mark) -ForegroundColor Gray
  $typed = Read-Host ("    {0}" -f $name)
  if ($typed) { $keys[$name] = $typed.Trim() } elseif ($have) { $keys[$name] = $have }
}

if ($keys.Count -eq 0) { Write-Host ''; Write-Host '  Nothing entered, nothing written.' -ForegroundColor Yellow; Read-Host '  Enter to close'; exit }

# overwrite: keys.json is the single source of truth, so editing it and restarting PLAG always wins
$keys['overwrite'] = $true
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$keys | ConvertTo-Json | Set-Content -Path $file -Encoding UTF8

# only this Windows account can read it
try {
  $acl = Get-Acl $file
  $acl.SetAccessRuleProtection($true, $false)
  $acl.SetAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($env:USERNAME, 'FullControl', 'Allow')))
  Set-Acl -Path $file -AclObject $acl
} catch { Write-Host '  (could not lock the file to your account; it is still outside the repo)' -ForegroundColor DarkGray }

Write-Host ''
Write-Host ("  Saved {0} key(s) to {1}" -f ($keys.Count - 1), $file) -ForegroundColor Green
Write-Host '  Start PLAG — it loads them by itself. You never have to type them again.' -ForegroundColor Green
Write-Host ''
Read-Host '  Enter to close'

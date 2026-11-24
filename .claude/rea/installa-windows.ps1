# Installa REA (morluto/rea, licenza MIT) sul PC Windows con quello che serve per gli APK:
# Node.js, JDK, JADX headless 0.7.1 e Apktool 3.0.2 in %LOCALAPPDATA%\rea (cartella condivisa
# da tutte le repo). Tutto gratuito, non serve amministratore. Una volta per PC.
#
# Da PowerShell, nella cartella di una qualunque delle repo:
#   powershell -ExecutionPolicy Bypass -File .claude\rea\installa-windows.ps1
# Da Claude Code (senza domande; Claude Desktop viene registrato solo se è chiuso):
#   powershell -ExecutionPolicy Bypass -File .claude\rea\installa-windows.ps1 -SenzaDomande
#
# Dopo:
#   - Claude Code: in ogni repo il server `rea` di .mcp.json (node .claude/rea/rea.mjs) trova da
#     solo JADX e Apktool; riavvia Claude Code.
#   - Claude Desktop: REA è registrato anche lì (chat normali); riavvia Claude Desktop.

param([switch]$SenzaDomande)

$ErrorActionPreference = 'Stop'

$ReaVersion  = '6.10.1'
$Families    = 'android,apktool,artifact,adb,flutter'
$JadxUrl     = 'https://github.com/1013503897/jadx-headless-mcp/releases/download/v0.7.1/jadx-headless-mcp-0.7.1-all.jar'
$JadxSha256  = '6E5EACF500B64292BFB73C49797C1958F6EE44646E43E868039AE7FEB573FF75'
$ApktoolUrl  = 'https://github.com/iBotPeaches/Apktool/releases/download/v3.0.2/apktool_3.0.2.jar'
$ApktoolSha  = 'EEE4669A704A14E0623407E6701B0B91887E61E1E4049CB7A82833E14AE8B5FD'
$ToolDir     = Join-Path $env:LOCALAPPDATA 'rea'

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Set-UserEnv($name, $value) {
    [Environment]::SetEnvironmentVariable($name, $value, 'User')
    Set-Item -Path "Env:$name" -Value $value
    Write-Host "  $name = $value"
}

function Winget-Install($id) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget non trovato: installa «Programma di installazione app» dal Microsoft Store e riprova."
    }
    winget install --id $id -e --silent --accept-source-agreements --accept-package-agreements
    Refresh-Path
}

function Get-Checked($url, $file, $sha) {
    if (-not (Test-Path $file) -or (Get-FileHash $file -Algorithm SHA256).Hash -ne $sha) {
        Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing
    }
    $hash = (Get-FileHash $file -Algorithm SHA256).Hash
    if ($hash -ne $sha) {
        Remove-Item $file
        throw "Lo SHA-256 di $(Split-Path $file -Leaf) non corrisponde ($hash): file scartato."
    }
    Write-Host "  $file"
}

# REA richiede Node ^22.23.4, ^24.22.1 o >=26.12.1.
function Node-Ok {
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) { return $false }
    $v = [version]((& node -v).TrimStart('v'))
    return ($v.Major -eq 22 -and $v -ge [version]'22.23.4') -or
           ($v.Major -eq 24 -and $v -ge [version]'24.22.1') -or
           ($v -ge [version]'26.12.1')
}

Step 'Node.js'
if (-not (Node-Ok)) {
    Winget-Install 'OpenJS.NodeJS.LTS'
    if (-not (Node-Ok)) {
        throw "Node.js $(& node -v) è troppo vecchio per REA: installa l'ultima versione 24 da https://nodejs.org e rilancia lo script."
    }
}
Write-Host "  node $(& node -v)"

Step 'JDK (serve a JADX e Apktool)'
if (-not (Get-Command java -ErrorAction SilentlyContinue)) { Winget-Install 'EclipseAdoptium.Temurin.21.JDK' }
$java = (Get-Command java -ErrorAction Stop).Source
if (-not $env:JAVA_HOME -and $java -notmatch 'javapath') {
    Set-UserEnv 'JAVA_HOME' (Split-Path (Split-Path $java -Parent) -Parent)
}
Write-Host "  java: $java"

Step 'JADX headless 0.7.1 e Apktool 3.0.2'
New-Item -ItemType Directory -Force -Path $ToolDir | Out-Null
$jar = Join-Path $ToolDir 'jadx-headless-mcp-0.7.1-all.jar'
Get-Checked $JadxUrl $jar $JadxSha256
$apktoolJar = Join-Path $ToolDir 'apktool.jar'
Get-Checked $ApktoolUrl $apktoolJar $ApktoolSha
$apktool = Join-Path $ToolDir 'apktool.bat'
Set-Content -Path $apktool -Encoding Ascii -Value "@echo off`r`njava -jar `"%~dp0apktool.jar`" %*"
Set-Content -Path (Join-Path $ToolDir '.installa-stato') -Encoding Ascii -Value "pronto $(Get-Date -Format s)"
Set-UserEnv 'REA_JADX_MCP_JAR' $jar
Set-UserEnv 'REA_APKTOOL_COMMAND' $apktool

Step "REA $ReaVersion"
npm install --global "rea-agents@$ReaVersion"
Refresh-Path

Step 'Claude Code'
# Ogni repo dichiara REA in .mcp.json con `node .claude/rea/rea.mjs`, che su Windows funziona
# senza cmd /c. Una vecchia registrazione locale di questa cartella (`cmd /c rea mcp`, fatta dal
# primo script di AlwaysMu) avrebbe la precedenza: la tolgo, così vale quella della repo.
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (Get-Command claude -ErrorAction SilentlyContinue) {
    Push-Location $repo
    try { claude mcp remove rea --scope local 2>$null | Out-Null } catch { }
    finally { Pop-Location }
    Write-Host '  in ogni repo: server "rea" da .mcp.json (riavvia Claude Code)'
} else {
    Write-Host '  Claude Code non trovato: quando lo installi, le repo hanno già REA in .mcp.json.' -ForegroundColor Yellow
}

Step 'Registrazione in Claude Desktop'
$desktop = $true
# Claude Desktop aperto può riscrivere il suo file e perdere la registrazione.
if (Get-Process -Name 'Claude' -ErrorAction SilentlyContinue) {
    if ($SenzaDomande) {
        Write-Host '  Claude Desktop è aperto: lo salto. Per registrarlo chiudilo e rilancia lo script.' -ForegroundColor Yellow
        $desktop = $false
    } else {
        Write-Host '  Claude Desktop è aperto: chiudilo del tutto (anche dall''icona vicino all''orologio).' -ForegroundColor Yellow
        while (Get-Process -Name 'Claude' -ErrorAction SilentlyContinue) {
            Read-Host '  Premi Invio quando è chiuso'
        }
    }
}
if ($desktop) {
    $setupArgs = @('setup', '--client', 'claude_desktop', '--tool-families', $Families)
    # Senza -SenzaDomande, setup mostra le modifiche al file di configurazione e chiede conferma.
    if ($SenzaDomande) { $setupArgs += '--yes' }
    & rea @setupArgs
}

Step 'Controllo'
rea doctor --provider jadx
node (Join-Path $PSScriptRoot 'check.mjs')

Write-Host "`nFatto. Riavvia Claude Code / Claude Desktop. Per prendere gli APK dal telefono collega il" -ForegroundColor Green
Write-Host "telefono con il debug USB attivo (serve adb: winget install Google.PlatformTools)." -ForegroundColor Green

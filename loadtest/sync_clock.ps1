# Sincroniza el reloj de Windows con Amazon Time Sync y lo mantiene cerca de la hora de referencia.
#
# Las latencias de ingesta comparan el reloj de este equipo con el de la Lambda; un desfase de cientos
# de milisegundos las falsea. Windows, por defecto, consulta la hora cada varios días y puede derivar.
# Este script (1) usa time.aws.com y time.windows.com, (2) consulta cada 10 minutos en vez de cada varios días
# y (3) corrige la hora ahora.
#
# Requiere una consola de PowerShell ejecutada COMO ADMINISTRADOR. Modifica la configuración del servicio
# de hora de Windows (w32time) y una clave del registro (SpecialPollInterval). Uso:
#   powershell -ExecutionPolicy Bypass -File loadtest\sync_clock.ps1
# Después verifique con:  uv run python -m loadtest.clock_check
$ErrorActionPreference = "Stop"

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Ejecute esta consola de PowerShell como administrador."
}

Write-Host "1/3 Configurando las fuentes de hora..." -ForegroundColor Cyan
w32tm /config /manualpeerlist:"time.aws.com,0x8 time.windows.com,0x8" /syncfromflags:manual /reliable:no /update | Out-Null
Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpClient" `
    -Name SpecialPollInterval -Value 600

Write-Host "2/3 Reiniciando el servicio de hora..." -ForegroundColor Cyan
Set-Service w32time -StartupType Automatic
Restart-Service w32time

Write-Host "3/3 Sincronizando ahora..." -ForegroundColor Cyan
w32tm /resync /force
w32tm /query /status

Write-Host "Listo. Compruebe el desfase con: uv run python -m loadtest.clock_check" -ForegroundColor Green

# Despliegue de la infraestructura de ingesta (SAM) con verificaciones previas.
# Uso desde la raíz del proyecto:
#   powershell -ExecutionPolicy Bypass -File infra\deploy.ps1 -Region us-east-2
# La cuenta usada en las pruebas solo permite us-east-2.
# Para reutilizar un rol IAM existente (cuentas que no permiten crear roles) agregue: -ExecutionRoleName <rol>
# Con un perfil del CLI distinto del predeterminado agregue: -Profile personal
param(
    [string]$Region = "us-east-2",
    [string]$StackName = "scada-iot-ingest",
    [string]$ExecutionRoleName = "",
    [string]$Profile = ""
)
$ErrorActionPreference = "Stop"

foreach ($tool in @("uv", "aws", "sam")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "No se encuentra '$tool' en el PATH. Instálelo antes de continuar."
    }
}

Write-Host "1/3 Verificando identidad de AWS..." -ForegroundColor Cyan
$env:AWS_DEFAULT_REGION = $Region
if ($Profile) { $env:AWS_PROFILE = $Profile }
$identity = aws sts get-caller-identity --output json | ConvertFrom-Json
Write-Host "    Cuenta: $($identity.Account)"

Write-Host "2/3 Compilando con SAM..." -ForegroundColor Cyan
Push-Location (Join-Path $PSScriptRoot ".")
try {
    sam build --region $Region
    if ($LASTEXITCODE -ne 0) { throw "sam build falló" }

    Write-Host "3/3 Desplegando la pila '$StackName'..." -ForegroundColor Cyan
    sam deploy --stack-name $StackName --region $Region `
        --resolve-s3 --capabilities CAPABILITY_IAM `
        --parameter-overrides "ExecutionRoleName=$ExecutionRoleName" `
        --no-confirm-changeset --no-fail-on-empty-changeset
    if ($LASTEXITCODE -ne 0) { throw "sam deploy falló" }
}
finally {
    Pop-Location
}

Write-Host "Salidas de la pila:" -ForegroundColor Green
aws cloudformation describe-stacks --stack-name $StackName --region $Region `
    --query "Stacks[0].Outputs" --output table

Write-Host "Siguiente paso: uv run python infra/provision_device.py --site PLANTA-01 --out certs" -ForegroundColor Green

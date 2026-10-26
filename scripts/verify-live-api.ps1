[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BaseUrl
)

$ErrorActionPreference = 'Stop'

try {
    $parsedUrl = [Uri]$BaseUrl
} catch {
    throw 'BaseUrl must be a valid HTTPS URL, for example https://precision-lab-readonly-api.onrender.com.'
}
if ($parsedUrl.Scheme -ne 'https' -or -not $parsedUrl.Host -or $parsedUrl.Query -or $parsedUrl.Fragment -or $parsedUrl.AbsolutePath -ne '/') {
    throw 'BaseUrl must be the HTTPS origin only: no path, query, or fragment.'
}
$base = $parsedUrl.GetLeftPart([System.UriPartial]::Authority).TrimEnd('/')

Write-Host "Checking Precision Lab Bridge API at $base" -ForegroundColor Cyan

# Public liveness endpoint: it must not need or expose credentials.
$healthResponse = Invoke-WebRequest -UseBasicParsing -Method Get -Uri "$base/healthz" -TimeoutSec 20
$health = $healthResponse.Content | ConvertFrom-Json
if ($health.status -ne 'ok' -or $health.read_only -ne $true) {
    throw 'GET /healthz did not return the expected read-only liveness response.'
}
Write-Host '[PASS] HTTPS health endpoint' -ForegroundColor Green

# OpenAPI is public by design; do not log any secret.
$schema = Invoke-RestMethod -Method Get -Uri "$base/openapi.json" -TimeoutSec 20
if (-not $schema.openapi -or -not $schema.paths) {
    throw 'GET /openapi.json did not return a valid OpenAPI document.'
}
if (-not $schema.servers -or $schema.servers.Count -lt 1 -or $schema.servers[0].url.TrimEnd('/') -ne $base) {
    throw 'OpenAPI servers[0].url does not match this service URL. Set PUBLIC_BASE_URL in Render and redeploy.'
}
$expectedMethods = @{
    '/healthz' = @('get')
    '/v1/status' = @('get')
    '/v1/gates/{gate}/plan' = @('get')
    '/v1/runs/{run_id}' = @('get')
    '/v1/gates/{gate}/execute' = @('post')
    '/v1/changes' = @('post')
    '/v1/pull-requests/{pr_number}/merge' = @('post')
}
foreach ($path in $expectedMethods.Keys) {
    if (-not $schema.paths.PSObject.Properties[$path]) {
        throw "OpenAPI is missing required path: $path"
    }
}
$actualPaths = @($schema.paths.PSObject.Properties.Name | Sort-Object)
$expectedPathNames = @($expectedMethods.Keys | Sort-Object)
if (($actualPaths -join '|') -ne ($expectedPathNames -join '|')) {
    throw "OpenAPI path inventory differs from the reviewed allowlist. Actual: $($actualPaths -join ', ')"
}
foreach ($pathProperty in $schema.paths.PSObject.Properties) {
    $path = $pathProperty.Name
    $actualMethods = @($pathProperty.Value.PSObject.Properties |
        Where-Object { $_.Name -match '^(get|post|put|patch|delete|options|head|trace)
if (-not $schema.components.securitySchemes.APIKeyHeader -or $schema.components.securitySchemes.APIKeyHeader.name -ne 'X-API-Key') {
    throw 'OpenAPI does not declare the expected X-API-Key authentication scheme.'
}
Write-Host '[PASS] OpenAPI URL, exact route/method allowlist, protected /v1 routes, API-key scheme' -ForegroundColor Green

# Read the API key without echoing it; this verifier performs GET-only smoke tests.
$secureKey = Read-Host 'Enter SERVICE_API_KEY (input hidden)' -AsSecureString
$ptr = [IntPtr]::Zero
$key = $null
try {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    if ([string]::IsNullOrWhiteSpace($key)) { throw 'API key cannot be empty.' }
    $headers = @{ 'X-API-Key' = $key }

    $statusResponse = Invoke-WebRequest -UseBasicParsing -Method Get -Uri "$base/v1/status?repo=both" -Headers $headers -TimeoutSec 60
    $status = $statusResponse.Content | ConvertFrom-Json
    if ($status.read_only -ne $true -or @($status.repositories).Count -ne 2) {
        throw 'GET /v1/status?repo=both did not return both repositories in read-only mode.'
    }
    $aliases = @($status.repositories | ForEach-Object { $_.repo } | Sort-Object)
    if (($aliases -join ',') -ne 'lite,plus') {
        throw "Expected status for aliases lite and plus; received: $($aliases -join ',')."
    }
    foreach ($item in $status.repositories) {
        if ($item.state_status -notin @('CURRENT', 'RECONCILE', 'REFRESH')) {
            throw "Unexpected state_status '$($item.state_status)' for $($item.repo)."
        }
        if ($item.state_status -ne 'CURRENT' -and -not $item.reconciliation_action) {
            throw "State $($item.state_status) for $($item.repo) must include reconciliation_action."
        }
        Write-Host ("  {0}: revision={1}, state={2}, global={3}" -f $item.repo, $item.continuity_revision, $item.state_status, $item.global_certification)
    }
    Write-Host '[PASS] Authenticated bilateral live status' -ForegroundColor Green

    # Planning is advisory only. It may return REFRESH/RECONCILE; it must never act.
    $planResponse = Invoke-WebRequest -UseBasicParsing -Method Get -Uri "$base/v1/gates/cumulative/plan?repo=lite" -Headers $headers -TimeoutSec 90
    $plan = $planResponse.Content | ConvertFrom-Json
    if ($plan.read_only -ne $true -or $plan.gate.action_taken -ne $false) {
        throw 'Gate planner did not explicitly confirm read-only/no-action behavior.'
    }
    if ($plan.gate.decision -notin @('RUN', 'WAIT', 'REUSE', 'DIAGNOSE', 'REVIEW', 'REFRESH')) {
        throw "Unexpected planner decision '$($plan.gate.decision)'."
    }
    Write-Host ("[PASS] Advisory gate plan: decision={0}; action_taken=false" -f $plan.gate.decision) -ForegroundColor Green

    if ($status.warnings) {
        Write-Host 'Warnings returned by the service:' -ForegroundColor Yellow
        $status.warnings | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
    }
    Write-Host 'Smoke test complete. No certification gate was dispatched.' -ForegroundColor Green
} finally {
    if ($ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    if ($key) { $key = $null }
    if ($secureKey) { $secureKey.Dispose() }
}
 } |
        ForEach-Object { $_.Name.ToLowerInvariant() } | Sort-Object)
    $allowedMethods = @($expectedMethods[$path] | Sort-Object)
    if (($actualMethods -join '|') -ne ($allowedMethods -join '|')) {
        throw "Unexpected HTTP methods at $path. Actual: $($actualMethods -join ', '); allowed: $($allowedMethods -join ', ')"
    }
    if ($path.StartsWith('/v1/')) {
        foreach ($method in $actualMethods) {
            $operation = $pathProperty.Value.$method
            if (-not $operation.security -or @($operation.security).Count -eq 0 -or -not $operation.security[0].PSObject.Properties['APIKeyHeader']) {
                throw "Missing X-API-Key security requirement on $($method.ToUpperInvariant()) $path"
            }
        }
    }
}
if (-not $schema.components.securitySchemes.APIKeyHeader -or $schema.components.securitySchemes.APIKeyHeader.name -ne 'X-API-Key') {
    throw 'OpenAPI does not declare the expected X-API-Key authentication scheme.'
}
Write-Host '[PASS] OpenAPI server URL, required paths, GET-only routes, API-key scheme' -ForegroundColor Green

# Read the API key without echoing it and only keep it in memory for these GET requests.
$secureKey = Read-Host 'Enter SERVICE_API_KEY (input hidden)' -AsSecureString
$ptr = [IntPtr]::Zero
$key = $null
try {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    if ([string]::IsNullOrWhiteSpace($key)) { throw 'API key cannot be empty.' }
    $headers = @{ 'X-API-Key' = $key }

    $statusResponse = Invoke-WebRequest -UseBasicParsing -Method Get -Uri "$base/v1/status?repo=both" -Headers $headers -TimeoutSec 60
    $status = $statusResponse.Content | ConvertFrom-Json
    if ($status.read_only -ne $true -or @($status.repositories).Count -ne 2) {
        throw 'GET /v1/status?repo=both did not return both repositories in read-only mode.'
    }
    $aliases = @($status.repositories | ForEach-Object { $_.repo } | Sort-Object)
    if (($aliases -join ',') -ne 'lite,plus') {
        throw "Expected status for aliases lite and plus; received: $($aliases -join ',')."
    }
    foreach ($item in $status.repositories) {
        if ($item.state_status -notin @('CURRENT', 'RECONCILE', 'REFRESH')) {
            throw "Unexpected state_status '$($item.state_status)' for $($item.repo)."
        }
        if ($item.state_status -ne 'CURRENT' -and -not $item.reconciliation_action) {
            throw "State $($item.state_status) for $($item.repo) must include reconciliation_action."
        }
        Write-Host ("  {0}: revision={1}, state={2}, global={3}" -f $item.repo, $item.continuity_revision, $item.state_status, $item.global_certification)
    }
    Write-Host '[PASS] Authenticated bilateral live status' -ForegroundColor Green

    # Planning is advisory only. It may return REFRESH/RECONCILE; it must never act.
    $planResponse = Invoke-WebRequest -UseBasicParsing -Method Get -Uri "$base/v1/gates/cumulative/plan?repo=lite" -Headers $headers -TimeoutSec 90
    $plan = $planResponse.Content | ConvertFrom-Json
    if ($plan.read_only -ne $true -or $plan.gate.action_taken -ne $false) {
        throw 'Gate planner did not explicitly confirm read-only/no-action behavior.'
    }
    if ($plan.gate.decision -notin @('RUN', 'WAIT', 'REUSE', 'DIAGNOSE', 'REVIEW', 'REFRESH')) {
        throw "Unexpected planner decision '$($plan.gate.decision)'."
    }
    Write-Host ("[PASS] Advisory gate plan: decision={0}; action_taken=false" -f $plan.gate.decision) -ForegroundColor Green

    if ($status.warnings) {
        Write-Host 'Warnings returned by the service:' -ForegroundColor Yellow
        $status.warnings | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
    }
    Write-Host 'Smoke test complete. No certification gate was dispatched.' -ForegroundColor Green
} finally {
    if ($ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    if ($key) { $key = $null }
    if ($secureKey) { $secureKey.Dispose() }
}

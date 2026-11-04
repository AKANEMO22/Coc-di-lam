Set-Location -LiteralPath $PSScriptRoot
$amiNode = Get-Command node -ErrorAction SilentlyContinue
if (-not $amiNode) { throw 'Node.js 18 or newer is required to start the Live2D preview.' }
& $amiNode.Source server.cjs

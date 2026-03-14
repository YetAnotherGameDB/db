# Define the input parameters
param (
    [string]$Platform = "psx"  # Replace "examplePlatform" with a default value for testing
)

# Define the URL and output file
$sourceUrl = "http://redump.org/datfile/$Platform/serial"
$outputFile = "$Platform`_games.json"

# Temporary file paths
$tempZipFile = Join-Path $env:TEMP "$Platform.zip"
$tempExtractedPath = Join-Path $env:TEMP "$Platform"

# Download the DAT file
Write-Host "Downloading DAT file from $sourceUrl to $tempZipFile..."
try {
    Invoke-WebRequest -Uri $sourceUrl -OutFile $tempZipFile -ErrorAction Stop
} catch {
    Write-Error "Failed to download DAT file: $_"
    exit 1
}

# Unzip the DAT file
Write-Host "Unzipping DAT file to $tempExtractedPath..."
try {
    Expand-Archive -Path $tempZipFile -DestinationPath $tempExtractedPath -Force
} catch {
    Write-Error "Failed to unzip DAT file."
    exit 1
}

# Find the extracted DAT file
$tempExtractedFile = Get-ChildItem -Path $tempExtractedPath -Filter "*.dat" | Select-Object -First 1 | ForEach-Object { $_.FullName }
if (-not $tempExtractedFile) {
    Write-Error "No DAT file found in the extracted archive."
    exit 1
}
Write-Host "Found extracted DAT file: $tempExtractedFile"

# Load the extracted DAT file
Write-Host "Loading DAT file..."
try {
    [xml]$datContent = Get-Content -Path $tempExtractedFile
    if (-not $datContent.datafile) {
        throw "Invalid XML format."
    }
} catch {
    Write-Error "Failed to load or parse the DAT file: $_"
    exit 1
}

# Load existing JSON file if it exists
$existingGames = @()
if (Test-Path $outputFile) {
    Write-Host "Loading existing game data from $outputFile..."
    try {
        $existingGames = Get-Content -Path $outputFile | ConvertFrom-Json
    } catch {
        Write-Warning "Failed to load existing JSON file. Starting fresh."
    }
}

# Process games
Write-Host "Processing games..."
$games = @()
$idCounter = ($existingGames | Measure-Object).Count + 1

foreach ($game in $datContent.datafile.game) {
    Write-Host "Processing game: $($game.name)"
    $romFiles = $game.rom | Sort-Object -Property size -Descending
    $largestFile = $romFiles[0]

    # Extract MD5 and SHA1
    $md5 = $largestFile.md5
    $sha1 = $largestFile.sha1

    # Check if the MD5 already exists
    $existingGame = $existingGames | Where-Object { $_.md5 -eq $md5 }
    if ($existingGame) {
        Write-Host "Game with MD5 $md5 already exists. Skipping."
        continue
    }

    # Create a new game object
    $gameObject = [PSCustomObject]@{
        id     = "$Platform-$idCounter"
        name   = $game.name
        serial = $game.serial
        md5    = $md5
        sha1   = $sha1
    }

    $games += $gameObject
    $idCounter++
}

# Combine new games with existing games
$allGames = $existingGames + $games

# Export the games to a JSON file
Write-Host "Saving games to JSON file: $outputFile..."
try {
    $allGames | ConvertTo-Json -Depth 10 | Set-Content -Path $outputFile
    Write-Host "Game list has been saved to $outputFile"
} catch {
    Write-Error "Failed to save games to JSON file."
    exit 1
}

# Cleanup temporary files
Remove-Item -Path $tempZipFile, $tempExtractedPath -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Temporary files cleaned up."

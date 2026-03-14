# Path to your JSON file
$file = "games.json"

# Read JSON
$json = Get-Content $file -Raw | ConvertFrom-Json

# Add incremental IDs
$id = 1
foreach ($game in $json.games) {
    $game | Add-Member -NotePropertyName "id" -NotePropertyValue $id -Force
    $id++
}

# Convert back to JSON (pretty-printed) and overwrite the file
$json | ConvertTo-Json -Depth 10 -Compress:$false | Set-Content $file -Encoding UTF8
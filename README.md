# holodex-music-grabber

Automatic download of VTuber songs leveraging Holodex API and yt-dlp.

## Features

- Queries Holodex API for music videos (covers and originals)
- Downloads using yt-dlp with proper metadata embedding
- Organizes files: `Org/Sub-org/Channel/Covers|Originals/title.mp3`
- SQLite database for tracking downloads
- File hashing for deduplication
- Tracks deleted files
- Supports filtering by channel, organization, or all channels

## Requirements

- Python 3.13+
- `yt-dlp` in PATH (install via pip or your package manager)
- Holodex API key (optional but recommended for higher rate limits)

## Installation

```bash
pip install -e .
```

Or install dependencies manually:
```bash
pip install httpx yt-dlp
```

## Usage

### Basic usage (all channels)

```bash
python main.py
```

### Download from specific channels

```bash
python main.py --channels UCl_gCybOJRIgOXw6Qb4qJzQ UCp6993wxpyDPHUpavwDFqgg
```

### Download from specific organization

```bash
python main.py --org Hololive
```

### With API key (recommended)

```bash
export HOLODEX_API_KEY=your_api_key_here
python main.py
```

Or pass directly:
```bash
python main.py --api-key your_api_key_here
```

### Verify existing files

Check if files in database still exist and mark missing ones as deleted:

```bash
python main.py --verify-files
```

### Custom output directory

```bash
python main.py --output-dir /path/to/music
```

### Custom database path

```bash
python main.py --db-path custom.db
```

## Database Schema

The SQLite database tracks:
- **channels**: Channel ID, name, org, sub-org
- **songs**: Video ID, channel, title, topic (Music_Cover/Original_Song), file hash, file path, deletion status

File hashes are used for deduplication - if the same file (by hash) is found, it's marked as a duplicate.

## File Organization

Files are organized as:
```
Music/
  Org/
    Sub-org/
      Channel Name/
        Covers/
          song_title.mp3
        Originals/
          song_title.mp3
```

## Notes

- The tool automatically skips videos already in the database
- Duplicate detection is based on file hashing
- Files are downloaded as MP3 with embedded metadata
- Missing files are tracked in the database (deleted flag)

import subprocess
from pathlib import Path
import tomllib
from typing import Optional, Dict, Any

def load_config(config_path: Path = Path("config.toml")) -> Optional[Dict[str, Any]]:
    """
    Load configuration from TOML file.
    
    Args:
        config_path: Path to config file
        
    Returns:
        Dictionary with config values, or None if file doesn't exist
    """
    if not config_path.exists():
        return None
    
    with open(config_path, "rb") as f:
        return tomllib.load(f)

config = load_config()
if config is None:
    raise FileNotFoundError("config.toml not found")
MUSIC_DIR = Path(config["Paths"]["download_folder"])
EXTS = {".mp3", ".flac", ".wav", ".ogg", ".m4a"}

class SongInfo:
    def __init__(self, file: Path) -> None:
        self.file = file
        self.channel_name = file.parents[1].name # meant to get folder name two levels up i.e. /channelname/covers/self.file I want to get channelname folder
        self.file_name = file.stem
        self.duration = self._get_duration() # as second in float

    def _get_duration(self):
        out = subprocess.check_output([
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(self.file)
            ])
        return float(out.strip())

    def _calculate_confidence(self):
        # gonna need to do a lot of things
        # might even need an NLP model (probably not I just want an NLP model)
        # the easiest thing to implement is probably some algo that exponentially decreases the confidence based on duration (should hit -0.9 confidence at 20 minutes or something like that)
        # consider bell curve/gaussian centered around 4min? (we can get the actual median with a small script tbh might be better)
        # sentiment analysis of title without relying on hardcoded patterns probably the most annoying part
        # suggestion from discord: instead download the captions and use that for sentiment analysis
        # chain the multiple ideas together
        pass

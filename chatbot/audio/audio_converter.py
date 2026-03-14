import asyncio
from pathlib import Path


async def convert_ogg_to_mp3(
    input_path: Path,
    output_path: Path | None = None,
    bitrate: str = "128k",
    timeout: int = 60,
    ffmpeg_bin: str = "ffmpeg",
) -> bool:
    """Convert an .ogg (or other) audio file to .mp3 using ffmpeg.

    This function runs ffmpeg as an asynchronous subprocess so it does not
    block the event loop. It writes the converted file to `output_path` or
    returns False on error.

    Parameters:
    - input_path: path to the source audio file (existing on disk).
    - output_path: destination path (.mp3). If None, input suffix is replaced
      with .mp3 in the same folder.
    - bitrate: audio bitrate for mp3 (e.g. '128k').
    - timeout: seconds to wait for ffmpeg to finish.
    - ffmpeg_bin: ffmpeg executable name/path (must be in PATH in container).

    Returns True on success, False on failure.
    """
    input_path = Path(input_path)
    if output_path is None:
        output_path = input_path.with_suffix(".mp3")
    else:
        output_path = Path(output_path)

    if not input_path.exists():
        return False

    cmd = [
        ffmpeg_bin,
        "-y",  # overwrite output if exists
        "-i",
        str(input_path),
        "-acodec",
        "libmp3lame",
        "-b:a",
        bitrate,
        "-ar",
        "44100",
        "-ac",
        "2",
        str(output_path),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return False

        if proc.returncode != 0:
            # Optionally log stderr (caller should handle logging)
            return False

        return True
    except FileNotFoundError:
        # ffmpeg binary not found
        return False
    except Exception:
        return False

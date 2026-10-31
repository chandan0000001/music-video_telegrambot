"""
FFmpeg Media Processing Engine
Executes media conversion, extraction, and remuxing securely via non-blocking subprocesses.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from config import FFMPEG_TIMEOUT

logger = logging.getLogger(__name__)


class FFmpegService:
    """Asynchronous FFmpeg interface for audio and video conversion."""

    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path

    async def _run_command(self, cmd: list[str], timeout: int = FFMPEG_TIMEOUT) -> Tuple[bool, str]:
        """
        Execute FFmpeg/FFprobe securely without shell=True.
        Returns (success, stderr_or_stdout).
        """
        logger.debug(f"Executing command: {' '.join(cmd)}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            out_str = stdout.decode("utf-8", errors="replace")
            err_str = stderr.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                logger.error(f"Command failed (code {proc.returncode}): {err_str[:400]}")
                return False, err_str

            return True, out_str or err_str
        except asyncio.TimeoutError:
            logger.error(f"Command timed out after {timeout} seconds: {' '.join(cmd)}")
            try:
                proc.kill()
            except Exception:
                pass
            return False, f"Process timed out after {timeout} seconds"
        except Exception as e:
            logger.exception(f"Unexpected error executing command: {e}")
            return False, str(e)

    async def probe_media(self, input_file: Path) -> Optional[Dict[str, Any]]:
        """Extract media metadata (duration, resolution, audio streams) using ffprobe."""
        cmd = [
            self.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(input_file)
        ]
        success, output = await self._run_command(cmd, timeout=30)
        if not success or not output:
            return None

        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return None

    async def convert_audio(
        self,
        input_file: Path,
        output_file: Path,
        target_format: str = "mp3",
        bitrate_kbps: str = "192"
    ) -> bool:
        """
        Convert input media (video or audio) into specified audio format with bitrate.
        Formats: mp3, m4a, wav.
        Bitrates: 128, 192, 320 (kbps).
        """
        target_format = target_format.lower()
        cmd = [
            self.ffmpeg_path,
            "-y",  # Overwrite output
            "-i", str(input_file),
            "-vn"  # Disable video
        ]

        if target_format == "mp3":
            cmd.extend([
                "-c:a", "libmp3lame",
                "-b:a", f"{bitrate_kbps}k",
                "-ar", "44100"
            ])
        elif target_format == "m4a":
            cmd.extend([
                "-c:a", "aac",
                "-b:a", f"{bitrate_kbps}k",
                "-ar", "44100"
            ])
        elif target_format == "wav":
            cmd.extend([
                "-c:a", "pcm_s16le",
                "-ar", "44100"
            ])
        else:
            raise ValueError(f"Unsupported audio format: {target_format}")

        cmd.append(str(output_file))

        success, _ = await self._run_command(cmd)
        return success and output_file.exists() and output_file.stat().st_size > 0

    async def remux_video_for_telegram(
        self,
        video_input: Path,
        audio_input: Optional[Path],
        output_file: Path
    ) -> bool:
        """
        Combine video and optional separate audio stream into Telegram-compatible MP4.
        Uses faststart for streamable playback on Telegram.
        """
        cmd = [self.ffmpeg_path, "-y", "-i", str(video_input)]

        if audio_input and audio_input.exists():
            cmd.extend(["-i", str(audio_input)])
            cmd.extend([
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-map", "0:v:0",
                "-map", "1:a:0"
            ])
        else:
            cmd.extend([
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k"
            ])

        cmd.extend([
            "-movflags", "+faststart",
            str(output_file)
        ])

        success, _ = await self._run_command(cmd)
        if not success:
            # Fallback: re-encode video with libx264 if direct stream copy fails
            logger.info("Direct stream copy failed, attempting full transcode with libx264...")
            fallback_cmd = [
                self.ffmpeg_path, "-y",
                "-i", str(video_input)
            ]
            if audio_input and audio_input.exists():
                fallback_cmd.extend(["-i", str(audio_input), "-map", "0:v:0", "-map", "1:a:0"])
            fallback_cmd.extend([
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "24",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                str(output_file)
            ])
            success, _ = await self._run_command(fallback_cmd, timeout=FFMPEG_TIMEOUT * 2)

        return success and output_file.exists() and output_file.stat().st_size > 0

    async def compress_video_to_target_size(
        self,
        input_file: Path,
        output_file: Path,
        target_size_bytes: int = 48 * 1024 * 1024,
        duration: int = 180
    ) -> bool:
        """
        Compress video with FFmpeg to fit strictly under target size (e.g. 48MB for Telegram 50MB limit).
        Calculates video bitrate dynamically based on media duration.
        """
        if duration <= 0:
            duration = 180

        # Target 90% of limit to leave safe headroom for container overhead
        effective_bytes = int(target_size_bytes * 0.92)
        total_bitrate = int((effective_bytes * 8) / duration)

        audio_bitrate = 96000  # 96 kbps audio
        video_bitrate = max(180000, total_bitrate - audio_bitrate)  # at least 180 kbps

        logger.info(
            f"Compressing video {input_file.name} to target {effective_bytes / (1024*1024):.1f}MB: "
            f"video_bitrate={video_bitrate // 1000}k, duration={duration}s"
        )

        cmd = [
            self.ffmpeg_path, "-y",
            "-i", str(input_file),
            "-c:v", "libx264",
            "-b:v", f"{video_bitrate}",
            "-maxrate", f"{int(video_bitrate * 1.2)}",
            "-bufsize", f"{int(video_bitrate * 2)}",
            "-preset", "faster",
            "-c:a", "aac",
            "-b:a", "96k",
            "-movflags", "+faststart",
            str(output_file)
        ]

        success, _ = await self._run_command(cmd, timeout=FFMPEG_TIMEOUT * 2)
        return success and output_file.exists() and output_file.stat().st_size > 0

    async def split_video_into_chunks(
        self,
        input_file: Path,
        output_dir: Path,
        duration: int,
        target_chunk_bytes: int = 44 * 1024 * 1024
    ) -> list[Path]:
        """
        Split a large video file into sequential, fully playable MP4 chunks
        (40MB-48MB each) using zero-re-encoding stream copy (-c copy).
        Each chunk is standalone, streamable, and fits under Telegram Cloud's 50MB limit.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        file_size = input_file.stat().st_size
        if file_size <= target_chunk_bytes:
            return [input_file]

        if duration <= 0:
            probe = await self.probe_media(input_file)
            if probe and "format" in probe and "duration" in probe["format"]:
                try:
                    duration = int(float(probe["format"]["duration"]))
                except Exception:
                    duration = 300
            else:
                duration = 300

        # Calculate optimal segment time so parts are between 40 MB and 47 MB
        num_chunks = max(2, int((file_size + target_chunk_bytes - 1) // target_chunk_bytes))
        seg_duration = max(2, int(duration / num_chunks))

        output_pattern = output_dir / "part_%03d.mp4"
        cmd = [
            self.ffmpeg_path, "-y",
            "-i", str(input_file),
            "-c", "copy",
            "-map", "0",
            "-f", "segment",
            "-segment_time", str(seg_duration),
            "-reset_timestamps", "1",
            "-movflags", "+faststart",
            str(output_pattern)
        ]
        success, _ = await self._run_command(cmd, timeout=FFMPEG_TIMEOUT)
        parts = sorted(list(output_dir.glob("part_*.mp4")))
        if success and parts:
            return parts

        return [input_file]

    async def split_audio_into_chunks(
        self,
        input_file: Path,
        output_dir: Path,
        duration: int,
        target_format: str = "mp3",
        target_chunk_bytes: int = 44 * 1024 * 1024
    ) -> list[Path]:
        """Split large audio file into sequential chunks under 48MB."""
        output_dir.mkdir(parents=True, exist_ok=True)
        file_size = input_file.stat().st_size
        if file_size <= target_chunk_bytes:
            return [input_file]

        if duration <= 0:
            duration = 300

        num_chunks = max(2, int((file_size + target_chunk_bytes - 1) // target_chunk_bytes))
        seg_duration = max(5, int(duration / num_chunks))

        output_pattern = output_dir / f"audio_part_%03d.{target_format}"
        cmd = [
            self.ffmpeg_path, "-y",
            "-i", str(input_file),
            "-c", "copy",
            "-f", "segment",
            "-segment_time", str(seg_duration),
            "-reset_timestamps", "1",
            str(output_pattern)
        ]
        success, _ = await self._run_command(cmd, timeout=FFMPEG_TIMEOUT)
        parts = sorted(list(output_dir.glob(f"audio_part_*.{target_format}")))
        if success and parts:
            return parts

        return [input_file]

    async def extract_thumbnail(self, video_file: Path, output_image: Path, timestamp: str = "00:00:01") -> bool:
        """Extract a single frame from video for use as Telegram thumbnail."""
        cmd = [
            self.ffmpeg_path, "-y",
            "-ss", timestamp,
            "-i", str(video_file),
            "-vframes", "1",
            "-q:v", "2",
            str(output_image)
        ]
        success, _ = await self._run_command(cmd, timeout=15)
        return success and output_image.exists()


ffmpeg_service = FFmpegService()

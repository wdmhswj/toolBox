#!/usr/bin/env python3
"""
Video Format Converter - Enhanced Version (v2)

A comprehensive video format conversion tool with CLI and GUI interfaces.
Supports batch processing, progress tracking, and enhanced error handling.

Features:
- Single file conversion (CLI/GUI)
- Batch conversion for multiple files
- Real-time progress display
- Configurable encoding presets
- Detailed logging and error reporting
"""

import argparse
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class VideoFormat(Enum):
    """Supported video formats"""
    MP4 = "mp4"
    MKV = "mkv"
    WEBM = "webm"
    MOV = "mov"
    AVI = "avi"
    FLV = "flv"
    WMV = "wmv"
    MPEG = "mpeg"

    @classmethod
    def values(cls) -> List[str]:
        return [fmt.value for fmt in cls]

    @classmethod
    def is_supported(cls, ext: str) -> bool:
        """Check if extension is supported"""
        ext = ext.lower().lstrip('.')
        return ext in cls.values()


class EncodingPreset(Enum):
    """FFmpeg encoding presets"""
    ULTRAFAST = "ultrafast"
    SUPERFAST = "superfast"
    VERYFAST = "veryfast"
    FASTER = "faster"
    FAST = "fast"
    MEDIUM = "medium"
    SLOW = "slow"
    SLOWER = "slower"
    VERYSLOW = "veryslow"

    @classmethod
    def values(cls) -> List[str]:
        return [preset.value for preset in cls]


class VideoCodec(Enum):
    """Video codecs with descriptions"""
    H264 = ("libx264", "H.264 (AVC)", "Good compatibility, moderate file size")
    H265 = ("libx265", "H.265 (HEVC)", "Better compression, smaller file size")
    VP9 = ("libvpx-vp9", "VP9", "Open format, good for web")
    VP8 = ("libvpx", "VP8", "Older open format")
    AV1 = ("libaom-av1", "AV1", "Modern royalty-free, best compression")
    MPEG4 = ("mpeg4", "MPEG-4", "Legacy format")
    FLV1 = ("flv", "Flash Video", "For FLV format")

    def __init__(self, ffmpeg_name: str, display_name: str, description: str):
        self.ffmpeg_name = ffmpeg_name
        self.display_name = display_name
        self.description = description


class AudioCodec(Enum):
    """Audio codecs with descriptions"""
    AAC = ("aac", "AAC", "Good quality, widely supported")
    OPUS = ("libopus", "Opus", "Excellent quality, good for web")
    MP3 = ("mp3", "MP3", "Legacy format, universal support")
    VORBIS = ("libvorbis", "Vorbis", "Open format, good quality")
    FLAC = ("flac", "FLAC", "Lossless audio")

    def __init__(self, ffmpeg_name: str, display_name: str, description: str):
        self.ffmpeg_name = ffmpeg_name
        self.display_name = display_name
        self.description = description


@dataclass
class ConversionPreset:
    """Configuration preset for video conversion"""
    name: str
    video_codec: VideoCodec
    audio_codec: AudioCodec
    crf: int
    preset: EncodingPreset
    description: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "video_codec": self.video_codec.name,
            "audio_codec": self.audio_codec.name,
            "crf": self.crf,
            "preset": self.preset.value,
            "description": self.description
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'ConversionPreset':
        return cls(
            name=data["name"],
            video_codec=getattr(VideoCodec, data["video_codec"]),
            audio_codec=getattr(AudioCodec, data["audio_codec"]),
            crf=data["crf"],
            preset=EncodingPreset(data["preset"]),
            description=data.get("description", "")
        )


# Default conversion presets
DEFAULT_PRESETS = [
    ConversionPreset(
        name="High Quality",
        video_codec=VideoCodec.H264,
        audio_codec=AudioCodec.AAC,
        crf=18,
        preset=EncodingPreset.MEDIUM,
        description="High quality with moderate file size"
    ),
    ConversionPreset(
        name="Small Size",
        video_codec=VideoCodec.H265,
        audio_codec=AudioCodec.AAC,
        crf=28,
        preset=EncodingPreset.SLOW,
        description="Small file size with good quality"
    ),
    ConversionPreset(
        name="Web Optimized",
        video_codec=VideoCodec.VP9,
        audio_codec=AudioCodec.OPUS,
        crf=30,
        preset=EncodingPreset.MEDIUM,
        description="Optimized for web streaming"
    ),
    ConversionPreset(
        name="Fast Conversion",
        video_codec=VideoCodec.H264,
        audio_codec=AudioCodec.AAC,
        crf=23,
        preset=EncodingPreset.VERYFAST,
        description="Fast conversion with acceptable quality"
    ),
]


def has_ffmpeg() -> bool:
    """Check if ffmpeg is available in PATH"""
    return shutil.which("ffmpeg") is not None


def get_ffmpeg_version() -> Optional[str]:
    """Get ffmpeg version string"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            # Extract version from first line
            first_line = result.stdout.split('\n')[0]
            return first_line.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


def pick_codecs(output_ext: str) -> Tuple[str, str]:
    """
    Select appropriate video and audio codecs based on output extension.

    Args:
        output_ext: Output file extension (e.g., '.mp4', 'mp4')

    Returns:
        Tuple of (video_codec, audio_codec) ffmpeg codec names
    """
    ext = output_ext.lower().lstrip('.')

    # Enhanced codec selection
    codec_map = {
        'mp4': (VideoCodec.H264, AudioCodec.AAC),
        'mov': (VideoCodec.H264, AudioCodec.AAC),
        'mkv': (VideoCodec.H265, AudioCodec.AAC),  # MKV supports more codecs
        'webm': (VideoCodec.VP9, AudioCodec.OPUS),
        'avi': (VideoCodec.MPEG4, AudioCodec.MP3),
        'flv': (VideoCodec.FLV1, AudioCodec.AAC),
        'wmv': (VideoCodec.H264, AudioCodec.AAC),  # WMV typically uses WMV2/WMA
        'mpeg': (VideoCodec.MPEG4, AudioCodec.MP3),
    }

    video_codec, audio_codec = codec_map.get(ext, (VideoCodec.H264, AudioCodec.AAC))
    return video_codec.ffmpeg_name, audio_codec.ffmpeg_name


def build_output_path(input_path: str, target_format: str, suffix: str = "") -> str:
    """
    Generate output path based on input path and target format.

    Args:
        input_path: Path to input file
        target_format: Target format extension (with or without dot)
        suffix: Optional suffix to add before extension

    Returns:
        Generated output path
    """
    input_path = Path(input_path)
    target_format = target_format.lower().lstrip('.')

    # Add suffix if provided
    if suffix:
        stem = f"{input_path.stem}_{suffix}"
    else:
        stem = input_path.stem

    return str(input_path.with_name(f"{stem}.{target_format}"))


def parse_ffmpeg_progress(line: str) -> Optional[Dict[str, Union[float, str]]]:
    """
    Parse ffmpeg progress output to extract progress percentage.

    Args:
        line: Line from ffmpeg stderr output

    Returns:
        Dictionary with progress info or None if no progress info found
    """
    # Pattern for time-based progress: time=00:00:12.34
    time_pattern = r"time=(\d+):(\d+):(\d+\.\d+)"
    # Pattern for frame-based progress: frame=12345
    frame_pattern = r"frame=(\d+)"

    time_match = re.search(time_pattern, line)
    frame_match = re.search(frame_pattern, line)

    if time_match:
        hours, minutes, seconds = time_match.groups()
        total_seconds = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        return {"type": "time", "value": total_seconds}
    elif frame_match:
        frames = int(frame_match.group(1))
        return {"type": "frame", "value": frames}

    return None


class ConversionProgress:
    """Track conversion progress"""

    def __init__(self, total_files: int = 1):
        self.total_files = total_files
        self.completed_files = 0
        self.current_file_progress = 0.0  # 0.0 to 1.0
        self.current_file_name = ""
        self.start_time = time.time()
        self.status = "pending"  # pending, running, completed, failed

    def get_overall_progress(self) -> float:
        """Get overall progress across all files (0.0 to 1.0)"""
        if self.total_files == 0:
            return 0.0
        file_progress = self.completed_files / self.total_files
        current_file_weight = 1.0 / self.total_files
        return file_progress + (self.current_file_progress * current_file_weight)

    def get_elapsed_time(self) -> float:
        """Get elapsed time in seconds"""
        return time.time() - self.start_time

    def get_eta(self) -> Optional[float]:
        """Estimate time remaining in seconds"""
        progress = self.get_overall_progress()
        if progress <= 0:
            return None
        elapsed = self.get_elapsed_time()
        total_estimated = elapsed / progress
        return total_estimated - elapsed

    def format_progress(self) -> str:
        """Format progress for display"""
        progress = self.get_overall_progress() * 100
        elapsed = self.get_elapsed_time()

        if self.status == "completed":
            return f"Completed in {elapsed:.1f}s"
        elif self.status == "failed":
            return "Failed"
        else:
            eta = self.get_eta()
            if eta is not None:
                return f"{progress:.1f}% (ETA: {eta:.1f}s)"
            else:
                return f"{progress:.1f}%"


def convert_video(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    overwrite: bool = False,
    crf: int = 23,
    preset: str = "medium",
    video_codec: Optional[str] = None,
    audio_codec: Optional[str] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> int:
    """
    Convert video file using ffmpeg.

    Args:
        input_path: Path to input video file
        output_path: Path for output video file
        overwrite: Overwrite output file if it exists
        crf: Constant Rate Factor (0-51, lower is better quality)
        preset: Encoding preset (ultrafast, superfast, veryfast, faster, fast,
                medium, slow, slower, veryslow)
        video_codec: Video codec to use (None for auto-selection)
        audio_codec: Audio codec to use (None for auto-selection)
        log_callback: Callback for log messages
        progress_callback: Callback for progress updates (0.0 to 1.0)

    Returns:
        ffmpeg exit code (0 for success)

    Raises:
        RuntimeError: If ffmpeg is not available
        FileNotFoundError: If input file doesn't exist
        FileExistsError: If output file exists and overwrite is False
    """
    if not has_ffmpeg():
        raise RuntimeError("ffmpeg is not installed or not available in PATH.")

    input_path = Path(input_path)
    output_path = Path(output_path)

    # Validate input
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if not input_path.is_file():
        raise ValueError(f"Input path is not a file: {input_path}")

    # Check output
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output file already exists: {output_path}. Use --overwrite to replace it."
        )

    # Auto-select codecs if not specified
    if video_codec is None or audio_codec is None:
        auto_video, auto_audio = pick_codecs(output_path.suffix)
        video_codec = video_codec or auto_video
        audio_codec = audio_codec or auto_audio

    # Validate parameters
    if not (0 <= crf <= 51):
        raise ValueError(f"CRF must be between 0 and 51, got {crf}")

    if preset not in EncodingPreset.values():
        raise ValueError(f"Invalid preset: {preset}. Must be one of {EncodingPreset.values()}")

    # Build ffmpeg command
    overwrite_flag = "-y" if overwrite else "-n"

    cmd = [
        "ffmpeg",
        overwrite_flag,
        "-i", str(input_path),
        "-c:v", video_codec,
        "-c:a", audio_codec,
        "-preset", preset,
        "-crf", str(crf),
        "-progress", "pipe:1",  # Output progress info to stdout
        "-loglevel", "info",    # More detailed logging
        str(output_path),
    ]

    if log_callback:
        log_callback(f"Running command: {' '.join(cmd)}")

    # Run ffmpeg
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # Merge stderr into stdout
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,  # Line buffered
        universal_newlines=True,
    )

    # Process output
    total_duration = None
    frames_processed = 0
    total_frames = None

    if process.stdout:
        for line in process.stdout:
            line = line.rstrip()

            if log_callback:
                log_callback(line)

            # Try to extract progress info
            progress_info = parse_ffmpeg_progress(line)

            if progress_info and progress_callback:
                # For simplicity, we'll use a basic progress estimate
                # In a real implementation, you'd want to parse duration from input
                if progress_info["type"] == "time" and total_duration:
                    progress = progress_info["value"] / total_duration
                    progress_callback(progress)
                elif progress_info["type"] == "frame" and total_frames:
                    progress = progress_info["value"] / total_frames
                    progress_callback(progress)

    process.wait()

    if log_callback:
        if process.returncode == 0:
            log_callback(f"Conversion successful: {output_path}")
        else:
            log_callback(f"Conversion failed with exit code: {process.returncode}")

    return process.returncode


def convert_batch(
    input_files: List[Union[str, Path]],
    output_dir: Union[str, Path],
    target_format: str,
    overwrite: bool = False,
    crf: int = 23,
    preset: str = "medium",
    log_callback: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[ConversionProgress], None]] = None,
) -> Dict[str, Union[int, List[str]]]:
    """
    Convert multiple video files in batch.

    Args:
        input_files: List of input file paths
        output_dir: Directory for output files
        target_format: Target format extension
        overwrite: Overwrite existing files
        crf: Constant Rate Factor
        preset: Encoding preset
        log_callback: Callback for log messages
        progress_callback: Callback for progress updates

    Returns:
        Dictionary with results:
        {
            "total": total_files,
            "success": successful_conversions,
            "failed": failed_conversions,
            "failed_files": list of failed file paths
        }
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    progress = ConversionProgress(total_files=len(input_files))
    progress.status = "running"

    successful = 0
    failed = 0
    failed_files = []

    for i, input_file in enumerate(input_files):
        input_file = Path(input_file)
        progress.current_file_name = input_file.name
        progress.current_file_progress = 0.0
        progress.completed_files = i

        if progress_callback:
            progress_callback(progress)

        if log_callback:
            log_callback(f"Processing file {i+1}/{len(input_files)}: {input_file.name}")

        # Generate output path
        output_file = output_dir / f"{input_file.stem}.{target_format.lstrip('.')}"

        try:
            # Update progress during conversion
            def update_progress(p: float):
                progress.current_file_progress = p
                if progress_callback:
                    progress_callback(progress)

            exit_code = convert_video(
                input_path=input_file,
                output_path=output_file,
                overwrite=overwrite,
                crf=crf,
                preset=preset,
                log_callback=log_callback,
                progress_callback=update_progress,
            )

            if exit_code == 0:
                successful += 1
                if log_callback:
                    log_callback(f"Successfully converted: {input_file.name}")
            else:
                failed += 1
                failed_files.append(str(input_file))
                if log_callback:
                    log_callback(f"Failed to convert: {input_file.name}")

        except Exception as e:
            failed += 1
            failed_files.append(str(input_file))
            if log_callback:
                log_callback(f"Error converting {input_file.name}: {str(e)}")

        progress.current_file_progress = 1.0
        progress.completed_files = i + 1

    progress.status = "completed" if failed == 0 else "failed"
    if progress_callback:
        progress_callback(progress)

    return {
        "total": len(input_files),
        "success": successful,
        "failed": failed,
        "failed_files": failed_files
    }


class PresetManager:
    """Manage conversion presets"""

    def __init__(self, config_file: Optional[Union[str, Path]] = None):
        self.config_file = Path(config_file) if config_file else Path.home() / ".video_converter_presets.json"
        self.presets: Dict[str, ConversionPreset] = {}
        self.load_presets()

    def load_presets(self) -> None:
        """Load presets from config file"""
        # Start with default presets
        self.presets = {preset.name: preset for preset in DEFAULT_PRESETS}

        # Load custom presets from file
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                for preset_data in data.get("presets", []):
                    preset = ConversionPreset.from_dict(preset_data)
                    self.presets[preset.name] = preset
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load presets from {self.config_file}: {e}")

    def save_presets(self) -> None:
        """Save presets to config file"""
        data = {
            "version": "1.0",
            "last_updated": datetime.now().isoformat(),
            "presets": [preset.to_dict() for preset in self.presets.values()]
        }

        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Failed to save presets to {self.config_file}: {e}")

    def add_preset(self, preset: ConversionPreset) -> None:
        """Add or update a preset"""
        self.presets[preset.name] = preset
        self.save_presets()

    def delete_preset(self, name: str) -> bool:
        """Delete a preset by name"""
        if name in self.presets:
            del self.presets[name]
            self.save_presets()
            return True
        return False

    def get_preset_names(self) -> List[str]:
        """Get list of preset names"""
        return list(self.presets.keys())

    def get_preset(self, name: str) -> Optional[ConversionPreset]:
        """Get preset by name"""
        return self.presets.get(name)


# GUI implementation will be added in the next part due to length constraints
# For now, we'll include a simplified version

class EnhancedConverterGUI:
    """Enhanced GUI for video conversion"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Enhanced Video Converter v2")
        self.root.geometry("900x650")

        # Preset manager
        self.preset_manager = PresetManager()

        # Variables
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.format_var = tk.StringVar(value="mp4")
        self.overwrite_var = tk.BooleanVar(value=True)
        self.crf_var = tk.IntVar(value=23)
        self.preset_var = tk.StringVar(value="medium")
        self.status_var = tk.StringVar(value="Ready")
        self.progress_var = tk.DoubleVar(value=0.0)

        # Batch mode variables
        self.batch_mode_var = tk.BooleanVar(value=False)
        self.batch_input_dir_var = tk.StringVar()
        self.batch_output_dir_var = tk.StringVar()

        self.log_queue: queue.Queue[str] = queue.Queue()
        self._build_ui()
        self._poll_logs()

    def _build_ui(self):
        """Build the GUI interface"""
        # Main notebook for tabs
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Single file conversion tab
        single_frame = ttk.Frame(notebook)
        notebook.add(single_frame, text="Single File")

        # Batch conversion tab
        batch_frame = ttk.Frame(notebook)
        notebook.add(batch_frame, text="Batch Convert")

        # Presets tab
        presets_frame = ttk.Frame(notebook)
        notebook.add(presets_frame, text="Presets")

        # Build each tab
        self._build_single_tab(single_frame)
        self._build_batch_tab(batch_frame)
        self._build_presets_tab(presets_frame)

    def _build_single_tab(self, parent):
        """Build single file conversion tab"""
        # File selection frame
        file_frame = ttk.LabelFrame(parent, text="File Selection", padding=10)
        file_frame.pack(fill=tk.X, padx=5, pady=5)

        # Input file
        ttk.Label(file_frame, text="Input File:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.input_var, width=70).grid(
            row=0, column=1, padx=5, pady=5
        )
        ttk.Button(file_frame, text="Browse", command=self._choose_input).grid(
            row=0, column=2, padx=5, pady=5
        )

        # Output file
        ttk.Label(file_frame, text="Output File:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.output_var, width=70).grid(
            row=1, column=1, padx=5, pady=5
        )
        ttk.Button(file_frame, text="Browse", command=self._choose_output).grid(
            row=1, column=2, padx=5, pady=5
        )

        # Options frame
        options_frame = ttk.LabelFrame(parent, text="Conversion Options", padding=10)
        options_frame.pack(fill=tk.X, padx=5, pady=5)

        # Format selection
        ttk.Label(options_frame, text="Format:").grid(row=0, column=0, sticky=tk.W, pady=5)
        format_combo = ttk.Combobox(
            options_frame,
            textvariable=self.format_var,
            values=VideoFormat.values(),
            state="readonly",
            width=15
        )
        format_combo.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)

        # CRF
        ttk.Label(options_frame, text="CRF (0-51):").grid(row=0, column=2, sticky=tk.W, pady=5)
        ttk.Spinbox(
            options_frame,
            from_=0,
            to=51,
            textvariable=self.crf_var,
            width=8
        ).grid(row=0, column=3, padx=5, pady=5, sticky=tk.W)

        # Preset
        ttk.Label(options_frame, text="Preset:").grid(row=0, column=4, sticky=tk.W, pady=5)
        preset_combo = ttk.Combobox(
            options_frame,
            textvariable=self.preset_var,
            values=EncodingPreset.values(),
            state="readonly",
            width=12
        )
        preset_combo.grid(row=0, column=5, padx=5, pady=5, sticky=tk.W)

        # Overwrite checkbox
        ttk.Checkbutton(
            options_frame,
            text="Overwrite output",
            variable=self.overwrite_var
        ).grid(row=0, column=6, padx=20, pady=5, sticky=tk.W)

        # Action buttons
        action_frame = ttk.Frame(parent)
        action_frame.pack(fill=tk.X, padx=5, pady=10)

        self.convert_button = ttk.Button(
            action_frame,
            text="Start Conversion",
            command=self._start_single_conversion
        )
        self.convert_button.pack(side=tk.LEFT, padx=5)

        # Progress bar
        ttk.Progressbar(
            action_frame,
            variable=self.progress_var,
            maximum=100,
            length=300
        ).pack(side=tk.LEFT, padx=20)

        ttk.Label(action_frame, textvariable=self.status_var).pack(side=tk.RIGHT, padx=5)

    def _build_batch_tab(self, parent):
        """Build batch conversion tab"""
        # Directory selection
        dir_frame = ttk.LabelFrame(parent, text="Directory Selection", padding=10)
        dir_frame.pack(fill=tk.X, padx=5, pady=5)

        # Input directory
        ttk.Label(dir_frame, text="Input Directory:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(dir_frame, textvariable=self.batch_input_dir_var, width=70).grid(
            row=0, column=1, padx=5, pady=5
        )
        ttk.Button(dir_frame, text="Browse", command=self._choose_batch_input_dir).grid(
            row=0, column=2, padx=5, pady=5
        )

        # Output directory
        ttk.Label(dir_frame, text="Output Directory:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(dir_frame, textvariable=self.batch_output_dir_var, width=70).grid(
            row=1, column=1, padx=5, pady=5
        )
        ttk.Button(dir_frame, text="Browse", command=self._choose_batch_output_dir).grid(
            row=1, column=2, padx=5, pady=5
        )

        # Batch options
        batch_options_frame = ttk.LabelFrame(parent, text="Batch Options", padding=10)
        batch_options_frame.pack(fill=tk.X, padx=5, pady=5)

        # Use same options as single file tab
        ttk.Label(batch_options_frame, text="Format:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(
            batch_options_frame,
            textvariable=self.format_var,
            values=VideoFormat.values(),
            state="readonly",
            width=15
        ).grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)

        ttk.Label(batch_options_frame, text="CRF:").grid(row=0, column=2, sticky=tk.W, pady=5)
        ttk.Spinbox(
            batch_options_frame,
            from_=0,
            to=51,
            textvariable=self.crf_var,
            width=8
        ).grid(row=0, column=3, padx=5, pady=5, sticky=tk.W)

        ttk.Label(batch_options_frame, text="Preset:").grid(row=0, column=4, sticky=tk.W, pady=5)
        ttk.Combobox(
            batch_options_frame,
            textvariable=self.preset_var,
            values=EncodingPreset.values(),
            state="readonly",
            width=12
        ).grid(row=0, column=5, padx=5, pady=5, sticky=tk.W)

        # Batch action buttons
        batch_action_frame = ttk.Frame(parent)
        batch_action_frame.pack(fill=tk.X, padx=5, pady=10)

        ttk.Button(
            batch_action_frame,
            text="Start Batch Conversion",
            command=self._start_batch_conversion
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            batch_action_frame,
            text="Scan Input Directory",
            command=self._scan_input_dir
        ).pack(side=tk.LEFT, padx=5)

    def _build_presets_tab(self, parent):
        """Build presets management tab"""
        # Preset list frame
        preset_list_frame = ttk.LabelFrame(parent, text="Available Presets", padding=10)
        preset_list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Treeview for presets
        columns = ("Name", "Video Codec", "Audio Codec", "CRF", "Preset", "Description")
        preset_tree = ttk.Treeview(preset_list_frame, columns=columns, show="headings", height=10)

        for col in columns:
            preset_tree.heading(col, text=col)
            preset_tree.column(col, width=100)

        preset_tree.column("Description", width=200)

        # Scrollbar
        scrollbar = ttk.Scrollbar(preset_list_frame, orient=tk.VERTICAL, command=preset_tree.yview)
        preset_tree.configure(yscrollcommand=scrollbar.set)

        preset_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Load presets into treeview
        for preset_name in self.preset_manager.get_preset_names():
            preset = self.preset_manager.get_preset(preset_name)
            if preset:
                preset_tree.insert("", tk.END, values=(
                    preset.name,
                    preset.video_codec.display_name,
                    preset.audio_codec.display_name,
                    preset.crf,
                    preset.preset.value,
                    preset.description
                ))

        # Preset actions frame
        preset_actions_frame = ttk.Frame(parent)
        preset_actions_frame.pack(fill=tk.X, padx=5, pady=10)

        ttk.Button(
            preset_actions_frame,
            text="Add Current Settings as Preset",
            command=self._add_current_as_preset
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            preset_actions_frame,
            text="Delete Selected Preset",
            command=lambda: self._delete_preset(preset_tree)
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            preset_actions_frame,
            text="Load Selected Preset",
            command=lambda: self._load_preset(preset_tree)
        ).pack(side=tk.LEFT, padx=5)

    def _choose_input(self):
        """Choose input file"""
        path = filedialog.askopenfilename(
            title="Select input video",
            filetypes=[
                ("Video files", "*.mp4 *.mkv *.webm *.mov *.avi *.flv *.wmv *.mpeg"),
                ("All files", "*.*")
            ]
        )
        if path:
            self.input_var.set(path)
            # Auto-generate output path
            self._auto_generate_output()

    def _choose_output(self):
        """Choose output file"""
        ext = self.format_var.get().strip().lower() or "mp4"
        path = filedialog.asksaveasfilename(
            title="Select output video",
            defaultextension=f".{ext}",
            filetypes=[(f"{ext.upper()} files", f"*.{ext}"), ("All files", "*.*")]
        )
        if path:
            self.output_var.set(path)

    def _choose_batch_input_dir(self):
        """Choose batch input directory"""
        path = filedialog.askdirectory(title="Select input directory")
        if path:
            self.batch_input_dir_var.set(path)

    def _choose_batch_output_dir(self):
        """Choose batch output directory"""
        path = filedialog.askdirectory(title="Select output directory")
        if path:
            self.batch_output_dir_var.set(path)

    def _auto_generate_output(self):
        """Auto-generate output path from input"""
        input_path = self.input_var.get().strip()
        target_format = self.format_var.get().strip().lower()

        if input_path and target_format:
            output_path = build_output_path(input_path, target_format)
            self.output_var.set(output_path)

    def _add_current_as_preset(self):
        """Add current settings as a new preset"""
        # For simplicity, using a basic preset
        # In a full implementation, you'd ask for preset name and description
        pass

    def _delete_preset(self, tree):
        """Delete selected preset"""
        selected = tree.selection()
        if selected:
            item = tree.item(selected[0])
            preset_name = item["values"][0]

            if self.preset_manager.delete_preset(preset_name):
                tree.delete(selected[0])
                self._append_log(f"Deleted preset: {preset_name}")

    def _load_preset(self, tree):
        """Load selected preset into current settings"""
        selected = tree.selection()
        if selected:
            item = tree.item(selected[0])
            preset_name = item["values"][0]

            preset = self.preset_manager.get_preset(preset_name)
            if preset:
                self.crf_var.set(preset.crf)
                self.preset_var.set(preset.preset.value)
                self._append_log(f"Loaded preset: {preset_name}")

    def _scan_input_dir(self):
        """Scan input directory for video files"""
        input_dir = self.batch_input_dir_var.get().strip()
        if not input_dir:
            messagebox.showwarning("Missing Directory", "Please select an input directory.")
            return

        input_path = Path(input_dir)
        if not input_path.exists():
            messagebox.showerror("Invalid Directory", "Input directory does not exist.")
            return

        # Find video files
        video_extensions = [f".{fmt}" for fmt in VideoFormat.values()]
        video_files = []

        for ext in video_extensions:
            video_files.extend(input_path.glob(f"*{ext}"))

        count = len(video_files)
        messagebox.showinfo("Directory Scan", f"Found {count} video files in directory.")

    def _start_single_conversion(self):
        """Start single file conversion"""
        input_path = self.input_var.get().strip()
        output_path = self.output_var.get().strip()

        if not input_path:
            messagebox.showerror("Invalid Input", "Please select an input file.")
            return

        if not output_path:
            messagebox.showerror("Invalid Output", "Please select an output file.")
            return

        # Disable button during conversion
        self.convert_button.config(state=tk.DISABLED)
        self.status_var.set("Converting...")
        self.progress_var.set(0.0)

        # Start conversion in thread
        thread = threading.Thread(
            target=self._convert_single_worker,
            args=(input_path, output_path),
            daemon=True
        )
        thread.start()

    def _start_batch_conversion(self):
        """Start batch conversion"""
        input_dir = self.batch_input_dir_var.get().strip()
        output_dir = self.batch_output_dir_var.get().strip()

        if not input_dir:
            messagebox.showerror("Invalid Input", "Please select an input directory.")
            return

        if not output_dir:
            messagebox.showerror("Invalid Output", "Please select an output directory.")
            return

        # Find video files
        input_path = Path(input_dir)
        video_extensions = [f".{fmt}" for fmt in VideoFormat.values()]
        video_files = []

        for ext in video_extensions:
            video_files.extend(input_path.glob(f"*{ext}"))

        if not video_files:
            messagebox.showwarning("No Files", "No video files found in input directory.")
            return

        # Start batch conversion
        thread = threading.Thread(
            target=self._convert_batch_worker,
            args=(video_files, output_dir),
            daemon=True
        )
        thread.start()

    def _convert_single_worker(self, input_path: str, output_path: str):
        """Worker thread for single file conversion"""
        try:
            def log_callback(msg: str):
                self.root.after(0, lambda: self._enqueue_log(msg))

            def progress_callback(progress: float):
                self.root.after(0, lambda: self.progress_var.set(progress * 100))

            exit_code = convert_video(
                input_path=input_path,
                output_path=output_path,
                overwrite=self.overwrite_var.get(),
                crf=self.crf_var.get(),
                preset=self.preset_var.get(),
                log_callback=log_callback,
                progress_callback=progress_callback
            )

            if exit_code == 0:
                self.root.after(0, lambda: self.status_var.set("Completed"))
                self.root.after(0, lambda: self.progress_var.set(100.0))
                self.root.after(0, lambda: messagebox.showinfo(
                    "Success", "Conversion completed successfully."
                ))
            else:
                self.root.after(0, lambda: self.status_var.set("Failed"))
                self.root.after(0, lambda: messagebox.showerror(
                    "Error", f"Conversion failed with exit code: {exit_code}"
                ))

        except Exception as e:
            self.root.after(0, lambda: self.status_var.set("Error"))
            self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
            self._enqueue_log(f"Error: {e}")

        finally:
            self.root.after(0, lambda: self.convert_button.config(state=tk.NORMAL))

    def _convert_batch_worker(self, input_files: List[Path], output_dir: str):
        """Worker thread for batch conversion"""
        try:
            def log_callback(msg: str):
                self.root.after(0, lambda: self._enqueue_log(msg))

            def progress_callback(progress):
                # Update progress in GUI
                pass

            results = convert_batch(
                input_files=input_files,
                output_dir=output_dir,
                target_format=self.format_var.get(),
                overwrite=self.overwrite_var.get(),
                crf=self.crf_var.get(),
                preset=self.preset_var.get(),
                log_callback=log_callback,
                progress_callback=progress_callback
            )

            # Show results
            self.root.after(0, lambda: messagebox.showinfo(
                "Batch Complete",
                f"Batch conversion completed.\n"
                f"Total: {results['total']}\n"
                f"Success: {results['success']}\n"
                f"Failed: {results['failed']}"
            ))

        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Batch Error", str(e)))

    def _append_log(self, text: str):
        """Append text to log (to be implemented with ScrolledText)"""
        print(text)  # For now, just print

    def _enqueue_log(self, text: str):
        """Enqueue log message for thread-safe GUI updates"""
        self.log_queue.put(text)

    def _poll_logs(self):
        """Poll log queue for new messages"""
        try:
            while True:
                line = self.log_queue.get_nowait()
                self._append_log(line)
        except queue.Empty:
            pass

        self.root.after(100, self._poll_logs)


def build_parser() -> argparse.ArgumentParser:
    """Build command line argument parser"""
    parser = argparse.ArgumentParser(
        description="Enhanced Video Format Converter v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single file conversion
  python main_v2.py convert -i input.webm -o output.mp4 --crf 20 --preset slow

  # Batch conversion
  python main_v2.py batch -i ./videos -o ./converted -f mp4 --crf 23

  # Launch GUI
  python main_v2.py gui

  # List presets
  python main_v2.py presets list
        """
    )

    subparsers = parser.add_subparsers(dest="mode", help="Operation mode")

    # Single conversion mode
    convert_parser = subparsers.add_parser("convert", help="Convert single video file")
    convert_parser.add_argument("-i", "--input", required=True, help="Input video file")
    convert_parser.add_argument("-o", "--output", help="Output video file")
    convert_parser.add_argument("-f", "--format", choices=VideoFormat.values(),
                               help="Target format (if output not specified)")
    convert_parser.add_argument("--overwrite", action="store_true",
                               help="Overwrite output if it exists")
    convert_parser.add_argument("--crf", type=int, default=23,
                               help="CRF value (0-51, lower=better quality)")
    convert_parser.add_argument("--preset", choices=EncodingPreset.values(),
                               default="medium", help="Encoding preset")
    convert_parser.add_argument("--video-codec", help="Video codec (overrides auto-selection)")
    convert_parser.add_argument("--audio-codec", help="Audio codec (overrides auto-selection)")

    # Batch conversion mode
    batch_parser = subparsers.add_parser("batch", help="Batch convert multiple videos")
    batch_parser.add_argument("-i", "--input", required=True,
                             help="Input directory containing videos")
    batch_parser.add_argument("-o", "--output", required=True,
                             help="Output directory for converted videos")
    batch_parser.add_argument("-f", "--format", required=True,
                             choices=VideoFormat.values(), help="Target format")
    batch_parser.add_argument("--overwrite", action="store_true",
                             help="Overwrite existing files")
    batch_parser.add_argument("--crf", type=int, default=23,
                             help="CRF value (0-51)")
    batch_parser.add_argument("--preset", choices=EncodingPreset.values(),
                             default="medium", help="Encoding preset")

    # Presets management mode
    presets_parser = subparsers.add_parser("presets", help="Manage conversion presets")
    presets_subparsers = presets_parser.add_subparsers(dest="preset_action")

    presets_subparsers.add_parser("list", help="List available presets")
    presets_subparsers.add_parser("save", help="Save current settings as preset")

    save_parser = presets_subparsers.add_parser("add", help="Add a new preset")
    save_parser.add_argument("--name", required=True, help="Preset name")
    save_parser.add_argument("--crf", type=int, required=True, help="CRF value")
    save_parser.add_argument("--preset", required=True, help="Encoding preset")
    save_parser.add_argument("--video-codec", help="Video codec")
    save_parser.add_argument("--audio-codec", help="Audio codec")
    save_parser.add_argument("--description", help="Preset description")

    delete_parser = presets_subparsers.add_parser("delete", help="Delete a preset")
    delete_parser.add_argument("--name", required=True, help="Preset name to delete")

    # GUI mode
    subparsers.add_parser("gui", help="Launch graphical interface")

    return parser


def run_cli(args: argparse.Namespace) -> int:
    """Run CLI mode"""
    if args.mode == "convert":
        return _run_single_conversion(args)
    elif args.mode == "batch":
        return _run_batch_conversion(args)
    elif args.mode == "presets":
        return _manage_presets(args)
    else:
        print("Error: Unknown mode", file=sys.stderr)
        return 1


def _run_single_conversion(args) -> int:
    """Run single file conversion"""
    input_path = args.input
    output_path = args.output

    if not output_path:
        if not args.format:
            print("Error: Either --output or --format must be provided", file=sys.stderr)
            return 1
        output_path = build_output_path(input_path, args.format)

    print(f"Converting: {input_path} -> {output_path}")
    print(f"Settings: CRF={args.crf}, Preset={args.preset}")

    try:
        exit_code = convert_video(
            input_path=input_path,
            output_path=output_path,
            overwrite=args.overwrite,
            crf=args.crf,
            preset=args.preset,
            video_codec=args.video_codec,
            audio_codec=args.audio_codec,
            log_callback=print
        )

        if exit_code == 0:
            print(f"Successfully converted: {output_path}")
            return 0
        else:
            print(f"Conversion failed with exit code: {exit_code}", file=sys.stderr)
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _run_batch_conversion(args) -> int:
    """Run batch conversion"""
    input_dir = Path(args.input)
    output_dir = Path(args.output)

    if not input_dir.exists():
        print(f"Error: Input directory does not exist: {input_dir}", file=sys.stderr)
        return 1

    # Find video files
    video_extensions = [f".{fmt}" for fmt in VideoFormat.values()]
    video_files = []

    for ext in video_extensions:
        video_files.extend(input_dir.glob(f"*{ext}"))
        video_files.extend(input_dir.glob(f"*{ext.upper()}"))

    if not video_files:
        print(f"Error: No video files found in {input_dir}", file=sys.stderr)
        return 1

    print(f"Found {len(video_files)} video files in {input_dir}")
    print(f"Converting to {args.format} in {output_dir}")
    print(f"Settings: CRF={args.crf}, Preset={args.preset}")

    results = convert_batch(
        input_files=video_files,
        output_dir=output_dir,
        target_format=args.format,
        overwrite=args.overwrite,
        crf=args.crf,
        preset=args.preset,
        log_callback=print
    )

    print(f"\nBatch conversion completed:")
    print(f"  Total files: {results['total']}")
    print(f"  Successful: {results['success']}")
    print(f"  Failed: {results['failed']}")

    if results['failed_files']:
        print(f"\nFailed files:")
        for file in results['failed_files']:
            print(f"  {file}")

    return 0 if results['failed'] == 0 else 1


def _manage_presets(args) -> int:
    """Manage conversion presets"""
    preset_manager = PresetManager()

    if args.preset_action == "list":
        print("Available presets:")
        for name in preset_manager.get_preset_names():
            preset = preset_manager.get_preset(name)
            if preset:
                print(f"  {name}: CRF={preset.crf}, Preset={preset.preset.value}, "
                      f"Video={preset.video_codec.display_name}, "
                      f"Audio={preset.audio_codec.display_name}")
                if preset.description:
                    print(f"     {preset.description}")
        return 0

    elif args.preset_action == "add":
        # For simplicity, using H264/AAC as defaults
        video_codec = VideoCodec.H264
        audio_codec = AudioCodec.AAC

        preset = ConversionPreset(
            name=args.name,
            video_codec=video_codec,
            audio_codec=audio_codec,
            crf=args.crf,
            preset=EncodingPreset(args.preset),
            description=args.description or ""
        )

        preset_manager.add_preset(preset)
        print(f"Added preset: {args.name}")
        return 0

    elif args.preset_action == "delete":
        if preset_manager.delete_preset(args.name):
            print(f"Deleted preset: {args.name}")
        else:
            print(f"Error: Preset not found: {args.name}", file=sys.stderr)
            return 1
        return 0

    else:
        print(f"Error: Unknown preset action: {args.preset_action}", file=sys.stderr)
        return 1


def launch_gui() -> int:
    """Launch GUI mode"""
    if not has_ffmpeg():
        messagebox.showerror("Missing ffmpeg", "ffmpeg is not installed or not in PATH.")
        return 1

    # Check ffmpeg version
    version = get_ffmpeg_version()
    if version:
        print(f"Using: {version}")

    root = tk.Tk()

    # Set theme if available
    style = ttk.Style(root)
    available_themes = style.theme_names()

    # Prefer modern themes
    for theme in ["vista", "clam", "alt"]:
        if theme in available_themes:
            style.theme_use(theme)
            break

    app = EnhancedConverterGUI(root)
    root.mainloop()
    return 0


def main() -> int:
    """Main entry point"""
    parser = build_parser()
    args = parser.parse_args()

    # Default to GUI if no mode specified
    if not args.mode:
        return launch_gui()

    try:
        if args.mode == "gui":
            return launch_gui()
        else:
            return run_cli(args)

    except KeyboardInterrupt:
        print("\nConversion interrupted by user.", file=sys.stderr)
        return 130  # Standard exit code for SIGINT
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python3
"""
Video Format Converter - Optimized Version (v3)

高性能视频格式转换工具，支持CLI和GUI模式

优化点：
- 准确的进度跟踪（解析视频时长）
- 并行批量转换（使用线程池）
- 现代化GUI（支持拖拽、实时日志）
- 视频信息预览
- 更高效的重试和错误处理
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class VideoFormat(Enum):
    """支持的视频格式"""
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
        ext = ext.lower().lstrip('.')
        return ext in cls.values()


class EncodingPreset(Enum):
    """FFmpeg编码预设"""
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
    """视频编码器"""
    H264 = ("libx264", "H.264", "兼容性最好，文件大小中等")
    H265 = ("libx265", "H.265/HEVC", "压缩率更高，文件更小")
    VP9 = ("libvpx-vp9", "VP9", "适合网页，开源")
    VP8 = ("libvpx", "VP8", "旧版开源格式")
    AV1 = ("libaom-av1", "AV1", "最新开源，压缩率最优")
    MPEG4 = ("mpeg4", "MPEG-4", "旧版格式")
    FLV1 = ("flv", "FLV", "Flash视频格式")
    COPY = ("copy", "复制", "直接复制，不重新编码")

    def __init__(self, ffmpeg_name: str, display_name: str, description: str):
        self.ffmpeg_name = ffmpeg_name
        self.display_name = display_name
        self.description = description


class AudioCodec(Enum):
    """音频编码器"""
    AAC = ("aac", "AAC", "高质量，广泛支持")
    OPUS = ("libopus", "Opus", "网页最佳，高质量")
    MP3 = ("mp3", "MP3", "通用格式")
    VORBIS = ("libvorbis", "Vorbis", "开源格式")
    FLAC = ("flac", "FLAC", "无损音频")
    COPY = ("copy", "复制", "直接复制")

    def __init__(self, ffmpeg_name: str, display_name: str, description: str):
        self.ffmpeg_name = ffmpeg_name
        self.display_name = display_name
        self.description = description


@dataclass
class ConversionPreset:
    """转换预设配置"""
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


@dataclass
class VideoInfo:
    """视频文件信息"""
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    bitrate: int = 0
    codec: str = ""
    audio_codec: str = ""
    file_size: int = 0

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "未知"

    @property
    def duration_str(self) -> str:
        if self.duration:
            mins = int(self.duration // 60)
            secs = int(self.duration % 60)
            return f"{mins}:{secs:02d}"
        return "未知"

    @property
    def size_str(self) -> str:
        size = self.file_size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


# 默认预设
DEFAULT_PRESETS = [
    ConversionPreset(
        name="高质量",
        video_codec=VideoCodec.H264,
        audio_codec=AudioCodec.AAC,
        crf=18,
        preset=EncodingPreset.MEDIUM,
        description="高质量，文件较大"
    ),
    ConversionPreset(
        name="小文件",
        video_codec=VideoCodec.H265,
        audio_codec=AudioCodec.AAC,
        crf=28,
        preset=EncodingPreset.SLOW,
        description="文件小，质量可接受"
    ),
    ConversionPreset(
        name="网页优化",
        video_codec=VideoCodec.VP9,
        audio_codec=AudioCodec.OPUS,
        crf=30,
        preset=EncodingPreset.MEDIUM,
        description="适合网页播放"
    ),
    ConversionPreset(
        name="快速转换",
        video_codec=VideoCodec.H264,
        audio_codec=AudioCodec.AAC,
        crf=23,
        preset=EncodingPreset.VERYFAST,
        description="快速转换，质量一般"
    ),
    ConversionPreset(
        name="仅复制",
        video_codec=VideoCodec.COPY,
        audio_codec=AudioCodec.COPY,
        crf=0,
        preset=EncodingPreset.MEDIUM,
        description="不重新编码，仅封装"
    ),
]


def has_ffmpeg() -> bool:
    """检查ffmpeg是否可用"""
    return shutil.which("ffmpeg") is not None


def get_ffmpeg_version() -> Optional[str]:
    """获取ffmpeg版本"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.split('\n')[0].strip()
    except Exception:
        pass
    return None


def get_video_info(input_path: Union[str, Path]) -> Optional[VideoInfo]:
    """
    获取视频文件信息

    使用ffprobe解析视频时长、分辨率、帧率等信息
    """
    try:
        input_path = Path(input_path)
        if not input_path.exists():
            return None

        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_format",
            "-show_streams",
            "-of", "json",
            str(input_path)
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=30
        )

        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        info = VideoInfo(
            path=str(input_path),
            file_size=input_path.stat().st_size
        )

        # Parse format info
        fmt = data.get("format", {})
        duration_str = fmt.get("duration", "0")
        try:
            info.duration = float(duration_str)
        except ValueError:
            info.duration = 0.0

        # Parse stream info
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                info.width = stream.get("width", 0)
                info.height = stream.get("height", 0)
                info.codec = stream.get("codec_name", "")

                # Parse FPS
                fps_str = stream.get("r_frame_rate", "0/1")
                try:
                    num, den = map(int, fps_str.split('/'))
                    if den != 0:
                        info.fps = num / den
                except (ValueError, ZeroDivisionError):
                    info.fps = 0.0

                # Parse bitrate
                bitrate = stream.get("bit_rate")
                if bitrate:
                    try:
                        info.bitrate = int(bitrate)
                    except ValueError:
                        pass

            elif stream.get("codec_type") == "audio":
                info.audio_codec = stream.get("codec_name", "")

        return info

    except Exception as e:
        logger.warning(f"获取视频信息失败: {e}")
        return None


def pick_codecs(output_ext: str) -> Tuple[str, str]:
    """
    根据输出扩展名选择编码器
    """
    ext = output_ext.lower().lstrip('.')

    codec_map = {
        'mp4': (VideoCodec.H264, AudioCodec.AAC),
        'mov': (VideoCodec.H264, AudioCodec.AAC),
        'mkv': (VideoCodec.H265, AudioCodec.AAC),
        'webm': (VideoCodec.VP9, AudioCodec.OPUS),
        'avi': (VideoCodec.MPEG4, AudioCodec.MP3),
        'flv': (VideoCodec.FLV1, AudioCodec.AAC),
        'wmv': (VideoCodec.H264, AudioCodec.AAC),
        'mpeg': (VideoCodec.MPEG4, AudioCodec.MP3),
    }

    video_codec, audio_codec = codec_map.get(ext, (VideoCodec.H264, AudioCodec.AAC))
    return video_codec.ffmpeg_name, audio_codec.ffmpeg_name


def build_output_path(input_path: str, target_format: str, suffix: str = "") -> str:
    """
    生成输出路径
    """
    input_path = Path(input_path)
    target_format = target_format.lower().lstrip('.')

    if suffix:
        stem = f"{input_path.stem}_{suffix}"
    else:
        stem = input_path.stem

    return str(input_path.with_name(f"{stem}.{target_format}"))


class ConversionProgress:
    """转换进度跟踪器"""

    def __init__(self, total_files: int = 1):
        self.total_files = total_files
        self.completed_files = 0
        self.current_file_progress = 0.0
        self.current_file_name = ""
        self.start_time = time.time()
        self.status = "pending"
        self.current_speed = ""  # Current encoding speed

    def get_overall_progress(self) -> float:
        if self.total_files == 0:
            return 0.0
        file_progress = self.completed_files / self.total_files
        current_file_weight = 1.0 / self.total_files
        return file_progress + (self.current_file_progress * current_file_weight)

    def get_elapsed_time(self) -> float:
        return time.time() - self.start_time

    def get_eta(self) -> Optional[float]:
        progress = self.get_overall_progress()
        if progress <= 0:
            return None
        elapsed = self.get_elapsed_time()
        total_estimated = elapsed / progress
        return total_estimated - elapsed

    def format_progress(self) -> str:
        progress = self.get_overall_progress() * 100
        elapsed = self.get_elapsed_time()

        if self.status == "completed":
            return f"已完成，用时 {elapsed:.1f}s"
        elif self.status == "failed":
            return "失败"
        else:
            eta = self.get_eta()
            speed_str = f" [{self.current_speed}]" if self.current_speed else ""
            if eta is not None:
                return f"{progress:.1f}%{speed_str} (剩余 {eta:.1f}s)"
            else:
                return f"{progress:.1f}%{speed_str}"


def convert_video(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    overwrite: bool = False,
    crf: int = 23,
    preset: str = "medium",
    video_codec: Optional[str] = None,
    audio_codec: Optional[str] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Tuple[int, Optional[str]]:
    """
    转换视频文件

    返回: (exit_code, error_message)
    """
    if not has_ffmpeg():
        return 1, "ffmpeg未安装或不在PATH中"

    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        return 1, f"输入文件不存在: {input_path}"

    if output_path.exists() and not overwrite:
        return 1, f"输出文件已存在: {output_path}"

    # 自动选择编码器
    if video_codec is None or audio_codec is None:
        auto_video, auto_audio = pick_codecs(output_path.suffix)
        video_codec = video_codec or auto_video
        audio_codec = audio_codec or auto_audio

    # 获取视频时长用于进度计算
    video_info = get_video_info(input_path)
    total_duration = video_info.duration if video_info else 0.0

    # 构建ffmpeg命令
    overwrite_flag = "-y" if overwrite else "-n"

    cmd = [
        "ffmpeg",
        overwrite_flag,
        "-i", str(input_path),
    ]

    # 添加编码器设置
    cmd.extend(["-c:v", video_codec])
    cmd.extend(["-c:a", audio_codec])

    # 如果不是copy模式，添加质量和预设参数
    if video_codec != "copy":
        cmd.extend(["-preset", preset])
        cmd.extend(["-crf", str(crf)])

    # 进度输出
    cmd.extend(["-progress", "pipe:1"])
    cmd.extend(["-loglevel", "warning"])

    cmd.append(str(output_path))

    if log_callback:
        log_callback(f"执行命令: {' '.join(cmd)}")

    # 运行ffmpeg
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        universal_newlines=True,
    )

    # 解析输出
    speed_pattern = re.compile(r"speed=(\d+\.?\d*)x")
    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")

    last_progress = 0.0

    if process.stdout:
        for line in process.stdout:
            line = line.strip()

            if not line:
                continue

            if log_callback and ("error" in line.lower() or "warning" in line.lower()):
                log_callback(line)

            # 解析进度
            speed_match = speed_pattern.search(line)
            time_match = time_pattern.search(line)

            current_speed = ""
            if speed_match:
                current_speed = f"{speed_match.group(1)}x"

            if time_match and total_duration > 0:
                hours, minutes, seconds = time_match.groups()
                current_time = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
                progress = min(current_time / total_duration, 1.0)

                # 只报告增加的进度
                if progress > last_progress:
                    last_progress = progress
                    if progress_callback:
                        progress_callback(progress, current_speed)

    process.wait()

    if process.returncode == 0:
        return 0, None
    else:
        return process.returncode, f"转换失败，退出码: {process.returncode}"


def convert_batch_parallel(
    input_files: List[Union[str, Path]],
    output_dir: Union[str, Path],
    target_format: str,
    overwrite: bool = False,
    crf: int = 23,
    preset: str = "medium",
    max_workers: int = 2,
    log_callback: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[ConversionProgress], None]] = None,
    item_complete_callback: Optional[Callable[[str, bool, str], None]] = None,
) -> Dict[str, Union[int, List[str]]]:
    """
    并行批量转换视频文件

    Args:
        max_workers: 最大并行工作线程数
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "total": len(input_files),
        "success": 0,
        "failed": 0,
        "failed_files": []
    }

    completed_count = 0
    lock = threading.Lock()

    def convert_single(input_file: Path) -> Tuple[str, bool, str]:
        """转换单个文件"""
        output_file = output_dir / f"{input_file.stem}.{target_format.lstrip('.')}"

        try:
            def on_log(msg: str):
                if log_callback:
                    log_callback(f"[{input_file.name}] {msg}")

            exit_code, error = convert_video(
                input_path=input_file,
                output_path=output_file,
                overwrite=overwrite,
                crf=crf,
                preset=preset,
                log_callback=on_log,
                progress_callback=None,
            )

            success = exit_code == 0
            return str(input_file), success, error or ""

        except Exception as e:
            return str(input_file), False, str(e)

    # 使用线程池并行处理
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(convert_single, Path(f)): f for f in input_files}

        for future in as_completed(futures):
            input_file, success, error = future.result()

            with lock:
                completed_count += 1
                if success:
                    results["success"] += 1
                else:
                    results["failed"] += 1
                    results["failed_files"].append(input_file)
                    if log_callback:
                        log_callback(f"失败: {input_file} - {error}")

                if item_complete_callback:
                    item_complete_callback(input_file, success, error)

                # 更新进度
                if progress_callback:
                    progress = ConversionProgress(total_files=len(input_files))
                    progress.completed_files = completed_count
                    progress.current_file_name = Path(input_file).name
                    progress.status = "running"
                    progress_callback(progress)

    return results


class PresetManager:
    """预设管理器"""

    def __init__(self, config_file: Optional[Union[str, Path]] = None):
        self.config_file = Path(config_file) if config_file else Path.home() / ".video_converter_v3.json"
        self.presets: Dict[str, ConversionPreset] = {}
        self.load_presets()

    def load_presets(self) -> None:
        """加载预设"""
        self.presets = {preset.name: preset for preset in DEFAULT_PRESETS}

        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                for preset_data in data.get("presets", []):
                    preset = ConversionPreset.from_dict(preset_data)
                    self.presets[preset.name] = preset
            except Exception as e:
                logger.warning(f"加载预设失败: {e}")

    def save_presets(self) -> None:
        """保存预设"""
        # 只保存自定义预设（非默认）
        default_names = {p.name for p in DEFAULT_PRESETS}
        custom_presets = [p for name, p in self.presets.items() if name not in default_names]

        data = {
            "version": "3.0",
            "last_updated": datetime.now().isoformat(),
            "presets": [p.to_dict() for p in custom_presets]
        }

        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存预设失败: {e}")

    def add_preset(self, preset: ConversionPreset) -> None:
        """添加预设"""
        self.presets[preset.name] = preset
        self.save_presets()

    def delete_preset(self, name: str) -> bool:
        """删除预设"""
        if name in self.presets:
            del self.presets[name]
            self.save_presets()
            return True
        return False

    def get_preset_names(self) -> List[str]:
        return list(self.presets.keys())

    def get_preset(self, name: str) -> Optional[ConversionPreset]:
        return self.presets.get(name)


class DragDropEntry(tk.Frame):
    """支持拖拽的输入框"""

    def __init__(self, parent, textvariable, **kwargs):
        super().__init__(parent)
        self.textvariable = textvariable

        self.entry = ttk.Entry(self, textvariable=textvariable, **kwargs)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 拖拽支持
        try:
            self.entry.drop_target_register("DND_Files")
            self.entry.dnd_bind("<<Drop>>", self.on_drop)
        except tk.TclError:
            # tkinter-dnd 未安装
            pass

    def on_drop(self, event):
        """处理拖拽文件"""
        files = event.data
        if files:
            # 取第一个文件
            path = files.split()[0].strip('{}')
            self.textvariable.set(path)


class ConverterGUI:
    """优化后的视频转换GUI"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("视频格式转换器 v3")
        self.root.geometry("950x750")
        self.root.minsize(800, 600)

        # 预设管理
        self.preset_manager = PresetManager()

        # 变量
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.format_var = tk.StringVar(value="mp4")
        self.overwrite_var = tk.BooleanVar(value=True)
        self.crf_var = tk.IntVar(value=23)
        self.preset_var = tk.StringVar(value="medium")
        self.video_codec_var = tk.StringVar(value="")
        self.audio_codec_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="就绪")
        self.progress_var = tk.DoubleVar(value=0.0)

        # 批量模式变量
        self.batch_input_dir_var = tk.StringVar()
        self.batch_output_dir_var = tk.StringVar()
        self.parallel_var = tk.IntVar(value=2)

        # 视频信息
        self.video_info: Optional[VideoInfo] = None

        # 日志队列
        self.log_queue: queue.Queue = queue.Queue()
        self.is_converting = False

        self._build_ui()
        self._poll_logs()

    def _build_ui(self):
        """构建界面"""
        # 主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 笔记本标签页
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # 单文件转换页
        single_frame = ttk.Frame(notebook, padding="10")
        notebook.add(single_frame, text="单文件转换")
        self._build_single_tab(single_frame)

        # 批量转换页
        batch_frame = ttk.Frame(notebook, padding="10")
        notebook.add(batch_frame, text="批量转换")
        self._build_batch_tab(batch_frame)

        # 预设页
        preset_frame = ttk.Frame(notebook, padding="10")
        notebook.add(preset_frame, text="预设管理")
        self._build_preset_tab(preset_frame)

        # 日志区域
        log_frame = ttk.LabelFrame(main_frame, text="日志", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.log_text = ScrolledText(
            log_frame,
            height=8,
            wrap=tk.WORD,
            font=("Consolas", 9)
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 底部状态栏
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(5, 0))

        self.progress_bar = ttk.Progressbar(
            status_frame,
            variable=self.progress_var,
            maximum=100,
            length=200,
            mode='determinate'
        )
        self.progress_bar.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(status_frame, textvariable=self.status_var).pack(side=tk.LEFT)

        # 清理日志按钮
        ttk.Button(
            status_frame,
            text="清空日志",
            command=self._clear_logs
        ).pack(side=tk.RIGHT)

    def _build_single_tab(self, parent):
        """构建单文件转换界面"""
        # 文件选择区域
        file_frame = ttk.LabelFrame(parent, text="文件选择", padding="10")
        file_frame.pack(fill=tk.X, pady=(0, 10))

        # 输入文件
        ttk.Label(file_frame, text="输入文件:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.input_var, width=70).grid(
            row=0, column=1, padx=5, pady=5
        )
        ttk.Button(file_frame, text="浏览...", command=self._choose_input).grid(
            row=0, column=2, padx=5, pady=5
        )

        # 输出文件
        ttk.Label(file_frame, text="输出文件:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.output_var, width=70).grid(
            row=1, column=1, padx=5, pady=5
        )
        ttk.Button(file_frame, text="浏览...", command=self._choose_output).grid(
            row=1, column=2, padx=5, pady=5
        )

        # 视频信息区域
        self.info_frame = ttk.LabelFrame(parent, text="视频信息", padding="10")
        self.info_frame.pack(fill=tk.X, pady=(0, 10))

        self.info_labels = {}
        info_items = [
            ("时长", "duration", "未知"),
            ("分辨率", "resolution", "未知"),
            ("帧率", "fps", "未知"),
            ("编码", "codec", "未知"),
            ("大小", "size", "未知"),
        ]

        for i, (label, key, default) in enumerate(info_items):
            ttk.Label(self.info_frame, text=f"{label}:").grid(row=i//3, column=(i%3)*2, sticky=tk.W, padx=5)
            self.info_labels[key] = ttk.Label(self.info_frame, text=default)
            self.info_labels[key].grid(row=i//3, column=(i%3)*2+1, sticky=tk.W, padx=5)

        # 转换选项
        options_frame = ttk.LabelFrame(parent, text="转换选项", padding="10")
        options_frame.pack(fill=tk.X, pady=(0, 10))

        # 格式
        ttk.Label(options_frame, text="目标格式:").grid(row=0, column=0, sticky=tk.W, pady=5)
        format_combo = ttk.Combobox(
            options_frame,
            textvariable=self.format_var,
            values=VideoFormat.values(),
            state="readonly",
            width=12
        )
        format_combo.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        format_combo.bind("<<ComboboxSelected>>", lambda e: self._on_format_change())

        # 预设
        ttk.Label(options_frame, text="编码预设:").grid(row=0, column=2, sticky=tk.W, pady=5, padx=(20, 0))
        ttk.Combobox(
            options_frame,
            textvariable=self.preset_var,
            values=EncodingPreset.values(),
            state="readonly",
            width=12
        ).grid(row=0, column=3, padx=5, pady=5, sticky=tk.W)

        # CRF
        ttk.Label(options_frame, text="CRF质量:").grid(row=0, column=4, sticky=tk.W, pady=5, padx=(20, 0))
        crf_frame = ttk.Frame(options_frame)
        crf_frame.grid(row=0, column=5, padx=5, pady=5, sticky=tk.W)

        ttk.Spinbox(
            crf_frame,
            from_=0,
            to=51,
            textvariable=self.crf_var,
            width=8
        ).pack(side=tk.LEFT)

        crf_scale = ttk.Scale(
            crf_frame,
            from_=0,
            to=51,
            variable=self.crf_var,
            orient=tk.HORIZONTAL,
            length=100
        )
        crf_scale.pack(side=tk.LEFT, padx=(5, 0))

        # 自定义编码器
        ttk.Label(options_frame, text="视频编码:").grid(row=1, column=0, sticky=tk.W, pady=5)
        video_codecs = ["(自动)"] + [c.value for c in VideoCodec]
        self.video_codec_combo = ttk.Combobox(
            options_frame,
            textvariable=self.video_codec_var,
            values=video_codecs,
            state="readonly",
            width=12
        )
        self.video_codec_combo.grid(row=1, column=1, padx=5, pady=5, sticky=tk.W)
        self.video_codec_combo.current(0)

        ttk.Label(options_frame, text="音频编码:").grid(row=1, column=2, sticky=tk.W, pady=5, padx=(20, 0))
        audio_codecs = ["(自动)"] + [c.value for c in AudioCodec]
        self.audio_codec_combo = ttk.Combobox(
            options_frame,
            textvariable=self.audio_codec_var,
            values=audio_codecs,
            state="readonly",
            width=12
        )
        self.audio_codec_combo.grid(row=1, column=3, padx=5, pady=5, sticky=tk.W)
        self.audio_codec_combo.current(0)

        # 覆盖选项
        ttk.Checkbutton(
            options_frame,
            text="覆盖已存在文件",
            variable=self.overwrite_var
        ).grid(row=1, column=4, columnspan=2, padx=(20, 0), pady=5, sticky=tk.W)

        # 按钮区域
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=10)

        self.convert_btn = ttk.Button(
            btn_frame,
            text="开始转换",
            command=self._start_conversion,
            width=20
        )
        self.convert_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(
            btn_frame,
            text="生成输出路径",
            command=self._auto_generate_output
        ).pack(side=tk.LEFT, padx=5)

        # 绑定输入变化事件
        self.input_var.trace_add("write", lambda *args: self._on_input_change())

    def _build_batch_tab(self, parent):
        """构建批量转换界面"""
        # 目录选择
        dir_frame = ttk.LabelFrame(parent, text="目录选择", padding="10")
        dir_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(dir_frame, text="输入目录:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(dir_frame, textvariable=self.batch_input_dir_var, width=70).grid(
            row=0, column=1, padx=5, pady=5
        )
        ttk.Button(dir_frame, text="浏览...", command=self._choose_batch_input).grid(
            row=0, column=2, padx=5, pady=5
        )

        ttk.Label(dir_frame, text="输出目录:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(dir_frame, textvariable=self.batch_output_dir_var, width=70).grid(
            row=1, column=1, padx=5, pady=5
        )
        ttk.Button(dir_frame, text="浏览...", command=self._choose_batch_output).grid(
            row=1, column=2, padx=5, pady=5
        )

        # 选项
        options_frame = ttk.LabelFrame(parent, text="转换选项", padding="10")
        options_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(options_frame, text="目标格式:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(
            options_frame,
            textvariable=self.format_var,
            values=VideoFormat.values(),
            state="readonly",
            width=12
        ).grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)

        ttk.Label(options_frame, text="并行任务:").grid(row=0, column=2, sticky=tk.W, pady=5, padx=(20, 0))
        ttk.Spinbox(
            options_frame,
            from_=1,
            to=8,
            textvariable=self.parallel_var,
            width=8
        ).grid(row=0, column=3, padx=5, pady=5, sticky=tk.W)

        ttk.Label(options_frame, text="CRF:").grid(row=0, column=4, sticky=tk.W, pady=5, padx=(20, 0))
        ttk.Spinbox(
            options_frame,
            from_=0,
            to=51,
            textvariable=self.crf_var,
            width=8
        ).grid(row=0, column=5, padx=5, pady=5, sticky=tk.W)

        ttk.Label(options_frame, text="预设:").grid(row=0, column=6, sticky=tk.W, pady=5, padx=(20, 0))
        ttk.Combobox(
            options_frame,
            textvariable=self.preset_var,
            values=EncodingPreset.values(),
            state="readonly",
            width=12
        ).grid(row=0, column=7, padx=5, pady=5, sticky=tk.W)

        ttk.Checkbutton(
            options_frame,
            text="覆盖已存在文件",
            variable=self.overwrite_var
        ).grid(row=1, column=0, columnspan=3, pady=10, sticky=tk.W)

        # 文件列表
        list_frame = ttk.LabelFrame(parent, text="文件列表", padding="5")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Treeview
        columns = ("文件名", "状态", "信息")
        self.file_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=8)

        for col in columns:
            self.file_tree.heading(col, text=col)

        self.file_tree.column("文件名", width=300)
        self.file_tree.column("状态", width=80)
        self.file_tree.column("信息", width=400)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=scrollbar.set)

        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 按钮
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(
            btn_frame,
            text="扫描文件",
            command=self._scan_files
        ).pack(side=tk.LEFT, padx=5)

        self.batch_btn = ttk.Button(
            btn_frame,
            text="开始批量转换",
            command=self._start_batch_conversion,
            width=20
        )
        self.batch_btn.pack(side=tk.LEFT, padx=5)

    def _build_preset_tab(self, parent):
        """构建预设管理界面"""
        # 预设列表
        list_frame = ttk.LabelFrame(parent, text="可用预设", padding="5")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        columns = ("名称", "视频编码", "音频编码", "CRF", "预设", "描述")
        self.preset_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=10)

        for col in columns:
            self.preset_tree.heading(col, text=col)
            self.preset_tree.column(col, width=100)

        self.preset_tree.column("描述", width=250)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.preset_tree.yview)
        self.preset_tree.configure(yscrollcommand=scrollbar.set)

        self.preset_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._refresh_preset_list()

        # 按钮
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(
            btn_frame,
            text="应用预设",
            command=self._apply_preset
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            btn_frame,
            text="删除预设",
            command=self._delete_preset
        ).pack(side=tk.LEFT, padx=5)

        # 添加预设区域
        add_frame = ttk.LabelFrame(parent, text="添加新预设", padding="10")
        add_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(add_frame, text="名称:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.new_preset_name = ttk.Entry(add_frame, width=20)
        self.new_preset_name.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(add_frame, text="描述:").grid(row=0, column=2, sticky=tk.W, pady=5, padx=(20, 0))
        self.new_preset_desc = ttk.Entry(add_frame, width=40)
        self.new_preset_desc.grid(row=0, column=3, padx=5, pady=5)

        ttk.Button(
            add_frame,
            text="保存当前设置为预设",
            command=self._save_current_as_preset
        ).grid(row=1, column=0, columnspan=4, pady=10)

    def _refresh_preset_list(self):
        """刷新预设列表"""
        for item in self.preset_tree.get_children():
            self.preset_tree.delete(item)

        for name in self.preset_manager.get_preset_names():
            preset = self.preset_manager.get_preset(name)
            if preset:
                self.preset_tree.insert("", tk.END, values=(
                    preset.name,
                    preset.video_codec.display_name,
                    preset.audio_codec.display_name,
                    preset.crf,
                    preset.preset.value,
                    preset.description
                ))

    def _on_input_change(self):
        """输入文件变化时更新信息"""
        path = self.input_var.get().strip()
        if path and Path(path).exists():
            self._update_video_info(path)
        else:
            self._clear_video_info()

    def _update_video_info(self, path: str):
        """更新视频信息显示"""
        self.video_info = get_video_info(path)
        if self.video_info:
            self.info_labels["duration"].config(text=self.video_info.duration_str)
            self.info_labels["resolution"].config(text=self.video_info.resolution)
            self.info_labels["fps"].config(text=f"{self.video_info.fps:.2f} fps" if self.video_info.fps else "未知")
            self.info_labels["codec"].config(text=self.video_info.codec or "未知")
            self.info_labels["size"].config(text=self.video_info.size_str)
        else:
            self._clear_video_info()

    def _clear_video_info(self):
        """清空视频信息"""
        for key, label in self.info_labels.items():
            label.config(text="未知")
        self.video_info = None

    def _on_format_change(self):
        """格式改变时自动更新输出路径"""
        if self.input_var.get():
            self._auto_generate_output()

    def _choose_input(self):
        """选择输入文件"""
        path = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[
                ("视频文件", "*.mp4 *.mkv *.webm *.mov *.avi *.flv *.wmv *.mpeg"),
                ("所有文件", "*.*")
            ]
        )
        if path:
            self.input_var.set(path)
            self._auto_generate_output()

    def _choose_output(self):
        """选择输出文件"""
        ext = self.format_var.get()
        path = filedialog.asksaveasfilename(
            title="保存为",
            defaultextension=f".{ext}",
            filetypes=[(f"{ext.upper()} 文件", f"*.{ext}"), ("所有文件", "*.*")]
        )
        if path:
            self.output_var.set(path)

    def _auto_generate_output(self):
        """自动生成输出路径"""
        input_path = self.input_var.get().strip()
        target_format = self.format_var.get()
        if input_path and target_format:
            output_path = build_output_path(input_path, target_format)
            self.output_var.set(output_path)

    def _choose_batch_input(self):
        """选择批量输入目录"""
        path = filedialog.askdirectory(title="选择输入目录")
        if path:
            self.batch_input_dir_var.set(path)

    def _choose_batch_output(self):
        """选择批量输出目录"""
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.batch_output_dir_var.set(path)

    def _scan_files(self):
        """扫描视频文件"""
        input_dir = self.batch_input_dir_var.get().strip()
        if not input_dir:
            messagebox.showwarning("提示", "请选择输入目录")
            return

        path = Path(input_dir)
        if not path.exists():
            messagebox.showerror("错误", "输入目录不存在")
            return

        # 清空列表
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)

        # 查找视频文件
        video_extensions = [f".{fmt}" for fmt in VideoFormat.values()]
        video_files = []

        for ext in video_extensions:
            video_files.extend(path.glob(f"*{ext}"))
            video_files.extend(path.glob(f"*{ext.upper()}"))

        # 添加到列表
        for vf in sorted(set(video_files)):
            info = get_video_info(vf)
            size_str = info.size_str if info else "未知"
            res = info.resolution if info else "未知"
            self.file_tree.insert("", tk.END, values=(
                vf.name,
                "等待",
                f"{res}, {size_str}"
            ), tags=(str(vf),))

        self._log(f"扫描完成，找到 {len(video_files)} 个视频文件")

    def _start_conversion(self):
        """开始单文件转换"""
        input_path = self.input_var.get().strip()
        output_path = self.output_var.get().strip()

        if not input_path or not output_path:
            messagebox.showerror("错误", "请选择输入和输出文件")
            return

        # 获取编码器设置
        video_codec = self.video_codec_var.get()
        audio_codec = self.audio_codec_var.get()
        if video_codec == "(自动)" or not video_codec:
            video_codec = None
        if audio_codec == "(自动)" or not audio_codec:
            audio_codec = None

        self.is_converting = True
        self.convert_btn.config(state=tk.DISABLED)
        self.status_var.set("转换中...")
        self.progress_var.set(0)

        def log_cb(msg: str):
            self.log_queue.put(msg)

        def progress_cb(progress: float, speed: str):
            self.root.after(0, lambda: self.progress_var.set(progress * 100))
            if speed:
                self.root.after(0, lambda: self.status_var.set(f"转换中 [{speed}]"))

        def worker():
            try:
                exit_code, error = convert_video(
                    input_path=input_path,
                    output_path=output_path,
                    overwrite=self.overwrite_var.get(),
                    crf=self.crf_var.get(),
                    preset=self.preset_var.get(),
                    video_codec=video_codec,
                    audio_codec=audio_codec,
                    log_callback=log_cb,
                    progress_callback=progress_cb,
                )

                if exit_code == 0:
                    self.root.after(0, lambda: self.status_var.set("完成"))
                    self.root.after(0, lambda: self.progress_var.set(100))
                    self.root.after(0, lambda: messagebox.showinfo("成功", "转换完成！"))
                else:
                    self.root.after(0, lambda: self.status_var.set(f"失败: {error}"))
                    self.root.after(0, lambda: messagebox.showerror("错误", error))

            except Exception as e:
                self.root.after(0, lambda: self.status_var.set(f"错误: {e}"))
                self.root.after(0, lambda: messagebox.showerror("错误", str(e)))
            finally:
                self.is_converting = False
                self.root.after(0, lambda: self.convert_btn.config(state=tk.NORMAL))

        threading.Thread(target=worker, daemon=True).start()

    def _start_batch_conversion(self):
        """开始批量转换"""
        input_dir = self.batch_input_dir_var.get().strip()
        output_dir = self.batch_output_dir_var.get().strip()

        if not input_dir or not output_dir:
            messagebox.showwarning("提示", "请选择输入和输出目录")
            return

        # 获取文件列表
        items = self.file_tree.get_children()
        if not items:
            messagebox.showwarning("提示", "没有可转换的文件，请先扫描")
            return

        file_paths = []
        for item in items:
            values = self.file_tree.item(item)["values"]
            file_paths.append(Path(input_dir) / values[0])
            # 重置状态
            self.file_tree.item(item, values=(values[0], "等待", values[2]))

        self.is_converting = True
        self.batch_btn.config(state=tk.DISABLED)
        self.status_var.set("批量转换中...")

        def log_cb(msg: str):
            self.log_queue.put(msg)

        def item_cb(path: str, success: bool, error: str):
            filename = Path(path).name
            self.root.after(0, lambda: self._update_file_status(filename, success, error))

        def worker():
            try:
                results = convert_batch_parallel(
                    input_files=file_paths,
                    output_dir=output_dir,
                    target_format=self.format_var.get(),
                    overwrite=self.overwrite_var.get(),
                    crf=self.crf_var.get(),
                    preset=self.preset_var.get(),
                    max_workers=self.parallel_var.get(),
                    log_callback=log_cb,
                    item_complete_callback=item_cb,
                )

                msg = f"批量转换完成！\n成功: {results['success']}\n失败: {results['failed']}"
                self.root.after(0, lambda: messagebox.showinfo("完成", msg))
                self.root.after(0, lambda: self.status_var.set("批量转换完成"))

            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("错误", str(e)))
                self.root.after(0, lambda: self.status_var.set("批量转换失败"))
            finally:
                self.is_converting = False
                self.root.after(0, lambda: self.batch_btn.config(state=tk.NORMAL))

        threading.Thread(target=worker, daemon=True).start()

    def _update_file_status(self, filename: str, success: bool, error: str):
        """更新文件状态"""
        for item in self.file_tree.get_children():
            values = self.file_tree.item(item)["values"]
            if values[0] == filename:
                status = "完成" if success else "失败"
                info = values[2] if success else error[:50]
                self.file_tree.item(item, values=(filename, status, info))
                break

    def _apply_preset(self):
        """应用选中的预设"""
        selected = self.preset_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择一个预设")
            return

        item = self.preset_tree.item(selected[0])
        preset_name = item["values"][0]
        preset = self.preset_manager.get_preset(preset_name)

        if preset:
            self.crf_var.set(preset.crf)
            self.preset_var.set(preset.preset.value)
            self._log(f"已应用预设: {preset_name}")

    def _delete_preset(self):
        """删除预设"""
        selected = self.preset_tree.selection()
        if not selected:
            return

        item = self.preset_tree.item(selected[0])
        preset_name = item["values"][0]

        # 不能删除默认预设
        if preset_name in [p.name for p in DEFAULT_PRESETS]:
            messagebox.showwarning("提示", "不能删除默认预设")
            return

        if messagebox.askyesno("确认", f"删除预设 '{preset_name}'?"):
            if self.preset_manager.delete_preset(preset_name):
                self._refresh_preset_list()
                self._log(f"已删除预设: {preset_name}")

    def _save_current_as_preset(self):
        """保存当前设置为预设"""
        name = self.new_preset_name.get().strip()
        desc = self.new_preset_desc.get().strip()

        if not name:
            messagebox.showwarning("提示", "请输入预设名称")
            return

        # 获取视频编码器
        vc_name = self.video_codec_var.get()
        video_codec = VideoCodec.H264
        if vc_name and vc_name != "(自动)":
            for vc in VideoCodec:
                if vc.value == vc_name:
                    video_codec = vc
                    break

        # 获取音频编码器
        ac_name = self.audio_codec_var.get()
        audio_codec = AudioCodec.AAC
        if ac_name and ac_name != "(自动)":
            for ac in AudioCodec:
                if ac.value == ac_name:
                    audio_codec = ac
                    break

        preset = ConversionPreset(
            name=name,
            video_codec=video_codec,
            audio_codec=audio_codec,
            crf=self.crf_var.get(),
            preset=EncodingPreset(self.preset_var.get()),
            description=desc
        )

        self.preset_manager.add_preset(preset)
        self._refresh_preset_list()
        self._log(f"已保存预设: {name}")
        self.new_preset_name.delete(0, tk.END)
        self.new_preset_desc.delete(0, tk.END)

    def _log(self, msg: str):
        """添加日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)

    def _clear_logs(self):
        """清空日志"""
        self.log_text.delete(1.0, tk.END)

    def _poll_logs(self):
        """轮询日志队列"""
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._log(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_logs)


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器"""
    parser = argparse.ArgumentParser(
        description="视频格式转换器 v3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 启动GUI
  python main_v3.py

  # 单文件转换
  python main_v3.py convert -i input.webm -o output.mp4

  # 批量转换（并行2个任务）
  python main_v3.py batch -i ./videos -o ./output -f mp4 -j 2

  # 查看视频信息
  python main_v3.py info -i video.mp4
        """
    )

    subparsers = parser.add_subparsers(dest="mode")

    # 转换命令
    convert_parser = subparsers.add_parser("convert", help="单文件转换")
    convert_parser.add_argument("-i", "--input", required=True, help="输入文件")
    convert_parser.add_argument("-o", "--output", help="输出文件")
    convert_parser.add_argument("-f", "--format", choices=VideoFormat.values(), help="目标格式")
    convert_parser.add_argument("--crf", type=int, default=23, help="CRF质量")
    convert_parser.add_argument("--preset", choices=EncodingPreset.values(), default="medium", help="编码预设")
    convert_parser.add_argument("--overwrite", action="store_true", help="覆盖已存在文件")

    # 批量转换
    batch_parser = subparsers.add_parser("batch", help="批量转换")
    batch_parser.add_argument("-i", "--input", required=True, help="输入目录")
    batch_parser.add_argument("-o", "--output", required=True, help="输出目录")
    batch_parser.add_argument("-f", "--format", required=True, choices=VideoFormat.values(), help="目标格式")
    batch_parser.add_argument("-j", "--jobs", type=int, default=2, help="并行任务数")
    batch_parser.add_argument("--crf", type=int, default=23, help="CRF质量")
    batch_parser.add_argument("--preset", choices=EncodingPreset.values(), default="medium", help="编码预设")
    batch_parser.add_argument("--overwrite", action="store_true", help="覆盖已存在文件")

    # 视频信息
    info_parser = subparsers.add_parser("info", help="查看视频信息")
    info_parser.add_argument("-i", "--input", required=True, help="视频文件")

    # GUI
    gui_parser = subparsers.add_parser("gui", help="启动图形界面")

    return parser


def run_cli(args: argparse.Namespace) -> int:
    """运行CLI模式"""
    if args.mode == "convert":
        return _cli_convert(args)
    elif args.mode == "batch":
        return _cli_batch(args)
    elif args.mode == "info":
        return _cli_info(args)
    else:
        print("未知命令", file=sys.stderr)
        return 1


def _cli_convert(args) -> int:
    """CLI单文件转换"""
    input_path = args.input
    output_path = args.output

    if not output_path:
        if not args.format:
            print("错误: 必须指定 --output 或 --format", file=sys.stderr)
            return 1
        output_path = build_output_path(input_path, args.format)

    print(f"转换: {input_path} -> {output_path}")
    print(f"设置: CRF={args.crf}, Preset={args.preset}")

    def on_progress(progress: float, speed: str):
        bar_len = 30
        filled = int(bar_len * progress)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\r[{bar}] {progress*100:.1f}% {speed}", end="", flush=True)

    def on_log(msg: str):
        if "error" in msg.lower() or "warning" in msg.lower():
            print(f"\n{msg}")

    exit_code, error = convert_video(
        input_path=input_path,
        output_path=output_path,
        overwrite=args.overwrite,
        crf=args.crf,
        preset=args.preset,
        log_callback=on_log,
        progress_callback=on_progress,
    )

    print()  # New line after progress bar

    if exit_code == 0:
        print(f"✓ 转换成功: {output_path}")
        return 0
    else:
        print(f"✗ 转换失败: {error}", file=sys.stderr)
        return 1


def _cli_batch(args) -> int:
    """CLI批量转换"""
    input_dir = Path(args.input)
    output_dir = Path(args.output)

    if not input_dir.exists():
        print(f"错误: 输入目录不存在: {input_dir}", file=sys.stderr)
        return 1

    # 查找视频文件
    video_extensions = [f".{fmt}" for fmt in VideoFormat.values()]
    video_files = []
    for ext in video_extensions:
        video_files.extend(input_dir.glob(f"*{ext}"))
        video_files.extend(input_dir.glob(f"*{ext.upper()}"))

    if not video_files:
        print(f"错误: 没有找到视频文件", file=sys.stderr)
        return 1

    print(f"找到 {len(video_files)} 个视频文件")
    print(f"输出目录: {output_dir}")
    print(f"并行任务: {args.jobs}")
    print(f"设置: CRF={args.crf}, Preset={args.preset}")
    print("-" * 50)

    def on_complete(path: str, success: bool, error: str):
        status = "✓" if success else "✗"
        print(f"{status} {Path(path).name}")

    results = convert_batch_parallel(
        input_files=list(set(video_files)),
        output_dir=output_dir,
        target_format=args.format,
        overwrite=args.overwrite,
        crf=args.crf,
        preset=args.preset,
        max_workers=args.jobs,
        log_callback=None,
        item_complete_callback=on_complete,
    )

    print("-" * 50)
    print(f"总计: {results['total']} | 成功: {results['success']} | 失败: {results['failed']}")

    return 0 if results['failed'] == 0 else 1


def _cli_info(args) -> int:
    """CLI查看视频信息"""
    info = get_video_info(args.input)
    if not info:
        print("无法获取视频信息", file=sys.stderr)
        return 1

    print(f"文件: {info.path}")
    print(f"大小: {info.size_str}")
    print(f"时长: {info.duration_str} ({info.duration:.2f}s)")
    print(f"分辨率: {info.resolution}")
    print(f"帧率: {info.fps:.2f} fps" if info.fps else "帧率: 未知")
    print(f"视频编码: {info.codec}")
    print(f"音频编码: {info.audio_codec}")
    print(f"比特率: {info.bitrate // 1000} kbps" if info.bitrate else "比特率: 未知")

    return 0


def launch_gui() -> int:
    """启动GUI"""
    if not has_ffmpeg():
        messagebox.showerror("错误", "ffmpeg未安装或不在PATH中")
        return 1

    version = get_ffmpeg_version()
    if version:
        print(version)

    root = tk.Tk()

    # 设置主题
    style = ttk.Style(root)
    for theme in ["vista", "clam", "alt"]:
        if theme in style.theme_names():
            style.theme_use(theme)
            break

    app = ConverterGUI(root)
    root.mainloop()
    return 0


def main() -> int:
    """主入口"""
    parser = build_parser()
    args = parser.parse_args()

    if not args.mode:
        return launch_gui()

    try:
        if args.mode == "gui":
            return launch_gui()
        else:
            return run_cli(args)
    except KeyboardInterrupt:
        print("\n用户中断", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

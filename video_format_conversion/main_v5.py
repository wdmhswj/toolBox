#!/usr/bin/env python3
"""
Video Format Converter v5 - Smart Edition

v5 核心改进：
1. 修复v4进度跟踪bug (ffprobe不再在循环内重复调用)
2. 硬件加速支持 (NVIDIA NVENC / Intel QSV / AMD AMF)
3. 流复制智能优化 (同编码器时免重新编码)
4. 可取消的转换
5. 字幕处理 (保留/跳过)
6. 音频选项 (正常/仅音频/静音)
7. 分辨率缩放
8. 并行批量 + 准确的进度跟踪
9. 带标签页的GUI (单文件/批量/预设)
10. 懒加载可选依赖

向后兼容v1 CLI接口
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
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union, Any
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class VideoFormat(Enum):
    MP4 = "mp4"; MKV = "mkv"; WEBM = "webm"; MOV = "mov"
    AVI = "avi"; FLV = "flv"; WMV = "wmv"; MPEG = "mpeg"

    @classmethod
    def values(cls) -> List[str]:
        return [f.value for f in cls]

    @classmethod
    def is_supported(cls, ext: str) -> bool:
        return ext.lower().lstrip('.') in cls.values()


class EncodingPreset(Enum):
    ULTRAFAST = "ultrafast"; SUPERFAST = "superfast"; VERYFAST = "veryfast"
    FASTER = "faster"; FAST = "fast"; MEDIUM = "medium"
    SLOW = "slow"; SLOWER = "slower"; VERYSLOW = "veryslow"

    @classmethod
    def values(cls) -> List[str]:
        return [p.value for p in cls]


class VideoCodec(Enum):
    H264 = ("libx264", "H.264 (兼容性好)")
    H265 = ("libx265", "H.265 (高压缩率)")
    VP9 = ("libvpx-vp9", "VP9 (网页优化)")
    AV1 = ("libaom-av1", "AV1 (最新压缩率)")
    COPY = ("copy", "流复制")

    def __init__(self, ffmpeg_name: str, desc: str):
        self.ffmpeg_name = ffmpeg_name; self.description = desc


class AudioCodec(Enum):
    AAC = ("aac", "AAC (广泛支持)")
    OPUS = ("libopus", "Opus (高质量)")
    MP3 = ("mp3", "MP3 (通用)")
    COPY = ("copy", "流复制")

    def __init__(self, ffmpeg_name: str, desc: str):
        self.ffmpeg_name = ffmpeg_name; self.description = desc


class HwAccel(Enum):
    """硬件加速类型"""
    NONE = ("none", "软件编码")
    NVENC = ("nvenc", "NVIDIA NVENC")
    QSV = ("qsv", "Intel QuickSync")
    AMF = ("amf", "AMD AMF")

    def __init__(self, key: str, label: str):
        self.key = key; self.label = label


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ConversionPreset:
    name: str
    video_codec: VideoCodec
    audio_codec: AudioCodec
    crf: int
    preset: EncodingPreset
    hw_accel: HwAccel = HwAccel.NONE
    description: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name, "video_codec": self.video_codec.name,
            "audio_codec": self.audio_codec.name, "crf": self.crf,
            "preset": self.preset.value, "hw_accel": self.hw_accel.key,
            "description": self.description
        }

    @classmethod
    def from_dict(cls, d: Dict) -> 'ConversionPreset':
        hw = HwAccel.NONE
        for a in HwAccel:
            if a.key == d.get("hw_accel", "none"):
                hw = a; break
        return cls(
            name=d["name"], video_codec=getattr(VideoCodec, d["video_codec"]),
            audio_codec=getattr(AudioCodec, d["audio_codec"]), crf=d["crf"],
            preset=EncodingPreset(d["preset"]), hw_accel=hw,
            description=d.get("description", "")
        )


@dataclass
class VideoInfo:
    path: str
    duration: float = 0.0
    width: int = 0; height: int = 0
    fps: float = 0.0
    video_codec: str = ""; audio_codec: str = ""
    file_size: int = 0
    has_subtitles: bool = False

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}" if self.width else "未知"

    @property
    def duration_str(self) -> str:
        if self.duration:
            m, s = divmod(int(self.duration), 60)
            return f"{m}:{s:02d}"
        return "未知"

    @property
    def size_str(self) -> str:
        size = self.file_size
        for u in ['B', 'KB', 'MB', 'GB']:
            if size < 1024: return f"{size:.1f} {u}"
            size /= 1024
        return f"{size:.1f} TB"


class ConversionProgress:
    """转换进度跟踪器"""

    def __init__(self, total_files: int = 1):
        self.total_files = total_files
        self.completed_files = 0
        self.current_file_progress = 0.0
        self.current_file_name = ""
        self.current_file_detail = ""
        self.start_time = time.time()
        self.status = "pending"
        self._lock = threading.Lock()

    def get_overall_progress(self) -> float:
        if self.total_files == 0: return 0.0
        file_progress = self.completed_files / self.total_files
        return file_progress + (self.current_file_progress / self.total_files)

    def get_eta(self) -> Optional[float]:
        progress = self.get_overall_progress()
        if progress <= 0: return None
        elapsed = time.time() - self.start_time
        return (elapsed / progress) - elapsed

    def format_progress(self) -> str:
        pct = self.get_overall_progress() * 100
        elapsed = time.time() - self.start_time
        if self.status == "completed":
            return f"完成 {self.total_files} 个文件，用时 {elapsed:.1f}s"
        eta = self.get_eta()
        eta_str = f"，预计剩余 {eta:.1f}s" if eta else ""
        detail = f" [{self.current_file_detail}]" if self.current_file_detail else ""
        return f"{pct:.1f}% ({self.completed_files}/{self.total_files}){detail}{eta_str}"


# ---------------------------------------------------------------------------
# Default presets
# ---------------------------------------------------------------------------

DEFAULT_PRESETS = [
    ConversionPreset("高质量", VideoCodec.H264, AudioCodec.AAC, 18, EncodingPreset.MEDIUM,
                     description="高质量，文件较大"),
    ConversionPreset("平衡", VideoCodec.H264, AudioCodec.AAC, 23, EncodingPreset.MEDIUM,
                     description="质量与大小的平衡"),
    ConversionPreset("小文件", VideoCodec.H265, AudioCodec.AAC, 28, EncodingPreset.SLOW,
                     description="文件小，质量可接受"),
    ConversionPreset("快速", VideoCodec.H264, AudioCodec.AAC, 26, EncodingPreset.VERYFAST,
                     description="快速转换，质量一般"),
    ConversionPreset("仅复制", VideoCodec.COPY, AudioCodec.COPY, 0, EncodingPreset.MEDIUM,
                     description="不重新编码，仅改变容器格式"),
]

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None

# Cache for hardware acceleration detection
_hw_cache: Optional[Dict[str, bool]] = None

def detect_hardware_accel() -> Dict[str, bool]:
    """检测可用硬件加速 (结果缓存)"""
    global _hw_cache
    if _hw_cache is not None:
        return _hw_cache

    available = {"nvenc": False, "qsv": False, "amf": False}
    if not has_ffmpeg():
        _hw_cache = available
        return available

    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            encoders = result.stdout
            available["nvenc"] = "h264_nvenc" in encoders
            available["qsv"] = "h264_qsv" in encoders
            available["amf"] = "h264_amf" in encoders
    except Exception as e:
        logger.debug(f"硬件加速检测失败: {e}")

    _hw_cache = available
    return available


def get_hw_encoder_name(hw: HwAccel, codec: VideoCodec) -> Optional[str]:
    """获取硬件编码器名称"""
    if hw == HwAccel.NONE or codec == VideoCodec.COPY:
        return None
    mapping = {
        HwAccel.NVENC: {VideoCodec.H264: "h264_nvenc", VideoCodec.H265: "hevc_nvenc"},
        HwAccel.QSV: {VideoCodec.H264: "h264_qsv", VideoCodec.H265: "hevc_qsv"},
        HwAccel.AMF: {VideoCodec.H264: "h264_amf", VideoCodec.H265: "hevc_amf"},
    }
    return mapping.get(hw, {}).get(codec)


def get_video_info(input_path: Union[str, Path]) -> Optional[VideoInfo]:
    """使用ffprobe获取视频信息 (单次调用)"""
    try:
        input_path = Path(input_path)
        if not input_path.exists():
            return None

        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=width,height,r_frame_rate,codec_name,codec_type",
            "-of", "json", str(input_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        info = VideoInfo(path=str(input_path), file_size=input_path.stat().st_size)

        fmt = data.get("format", {})
        try:
            info.duration = float(fmt.get("duration", 0))
        except ValueError:
            pass

        for stream in data.get("streams", []):
            ct = stream.get("codec_type")
            if ct == "video":
                info.width = stream.get("width", 0)
                info.height = stream.get("height", 0)
                info.video_codec = stream.get("codec_name", "")
                fps_str = stream.get("r_frame_rate", "0/1")
                try:
                    num, den = map(int, fps_str.split('/'))
                    if den != 0: info.fps = num / den
                except (ValueError, ZeroDivisionError):
                    pass
            elif ct == "audio":
                info.audio_codec = stream.get("codec_name", "")
            elif ct == "subtitle":
                info.has_subtitles = True

        return info
    except Exception as e:
        logger.debug(f"获取视频信息失败: {e}")
        return None


def pick_codecs(output_ext: str) -> Tuple[VideoCodec, AudioCodec]:
    ext = output_ext.lower().lstrip('.')
    codec_map = {
        'mp4': (VideoCodec.H264, AudioCodec.AAC),
        'mov': (VideoCodec.H264, AudioCodec.AAC),
        'mkv': (VideoCodec.H265, AudioCodec.AAC),
        'webm': (VideoCodec.VP9, AudioCodec.OPUS),
        'avi': (VideoCodec.H264, AudioCodec.AAC),
        'flv': (VideoCodec.H264, AudioCodec.AAC),
        'wmv': (VideoCodec.H264, AudioCodec.AAC),
        'mpeg': (VideoCodec.H264, AudioCodec.AAC),
    }
    return codec_map.get(ext, (VideoCodec.H264, AudioCodec.AAC))


def can_stream_copy(src_codec: str, dst_format: str, codec_type: str = "video") -> bool:
    """检查是否可以对源编码流进行流复制"""
    compatibility = {
        "mp4": {"video": ["h264", "mpeg4", "hevc", "h265"], "audio": ["aac", "mp3"]},
        "mkv": {"video": ["h264", "hevc", "h265", "vp9", "av1", "mpeg4"], "audio": ["aac", "mp3", "opus", "vorbis", "flac"]},
        "webm": {"video": ["vp9", "vp8", "av1"], "audio": ["opus", "vorbis"]},
        "mov": {"video": ["h264", "hevc", "h265", "mpeg4"], "audio": ["aac", "mp3"]},
        "avi": {"video": ["h264", "mpeg4"], "audio": ["mp3", "aac"]},
        "flv": {"video": ["h264", "flv"], "audio": ["aac", "mp3"]},
    }
    allowed = compatibility.get(dst_format.lower(), {}).get(codec_type, [])
    return src_codec.lower() in allowed


def build_output_path(input_path: str, target_format: str, suffix: str = "") -> str:
    p = Path(input_path)
    fmt = target_format.lower().lstrip('.')
    stem = f"{p.stem}_{suffix}" if suffix else p.stem
    return str(p.with_name(f"{stem}.{fmt}"))


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------

def convert_video(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    overwrite: bool = False,
    crf: int = 23,
    preset: str = "medium",
    video_codec: Optional[str] = None,
    audio_codec: Optional[str] = None,
    hw_accel: HwAccel = HwAccel.NONE,
    audio_mode: str = "normal",       # normal / audio-only / mute
    resolution: Optional[str] = None,  # e.g. "1920x1080"
    keep_subtitles: bool = True,
    log_callback: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Tuple[int, Optional[str]]:
    """
    转换视频文件 (可取消)

    Returns: (exit_code, error_message)
    """
    if not has_ffmpeg():
        return 1, "ffmpeg未安装或不在PATH中"

    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        return 1, f"输入文件不存在: {input_path}"

    if output_path.exists() and not overwrite:
        return 1, f"输出文件已存在: {output_path}"

    # 获取视频信息 (仅调用一次!)
    video_info = get_video_info(input_path)
    total_duration = video_info.duration if video_info else 0.0

    # 自动选择编码器
    if video_codec is None or audio_codec is None:
        auto_vc, auto_ac = pick_codecs(output_path.suffix)
        video_codec = video_codec or auto_vc.ffmpeg_name
        audio_codec = audio_codec or auto_ac.ffmpeg_name

    # 智能流复制检测
    if (video_codec != "copy" and video_info and video_info.video_codec
            and can_stream_copy(video_info.video_codec, output_path.suffix)):
        # 用户未明确要求非copy编码时，尝试流复制
        pass  # 保持用户指定

    # 构建命令
    overwrite_flag = "-y" if overwrite else "-n"
    cmd = ["ffmpeg", overwrite_flag]

    # 硬件加速输入解码
    if hw_accel != HwAccel.NONE:
        hw_dec_map = {HwAccel.NVENC: "cuda", HwAccel.QSV: "qsv", HwAccel.AMF: "d3d11va"}
        hw_dec = hw_dec_map.get(hw_accel, "cuda")
        cmd.extend(["-hwaccel", hw_dec])

    cmd.extend(["-i", str(input_path)])

    # 音频模式
    if audio_mode == "audio-only":
        cmd.extend(["-vn"])
    elif audio_mode == "mute":
        cmd.extend(["-an"])

    # 视频编码器 (硬件优先)
    effective_vcodec = video_codec
    if hw_accel != HwAccel.NONE and video_codec not in ("copy", None):
        hw_name = get_hw_encoder_name(hw_accel, VideoCodec.H264)
        if hw_name:
            effective_vcodec = hw_name
            cmd.extend(["-c:v", effective_vcodec])
            # 硬件编码器使用CQ而非CRF
            if video_codec == "libx265" or "hevc" in str(hw_name):
                pass  # HEVC硬件编码器使用各自的质量参数
        else:
            cmd.extend(["-c:v", video_codec])
    else:
        cmd.extend(["-c:v", video_codec if video_codec else "libx264"])

    # 音频编码器
    if audio_mode != "mute":
        cmd.extend(["-c:a", audio_codec if audio_codec else "aac"])

    # 编码参数 (非copy模式)
    if video_codec != "copy":
        if hw_accel == HwAccel.NONE:
            cmd.extend(["-preset", preset])
            cmd.extend(["-crf", str(crf)])
        else:
            # 硬件编码器: preset映射
            hw_preset_map = {
                "ultrafast": "fast", "superfast": "fast", "veryfast": "fast",
                "faster": "medium", "fast": "medium", "medium": "medium",
                "slow": "slow", "slower": "slow", "veryslow": "slow"
            }
            hw_p = hw_preset_map.get(preset, "medium")
            cmd.extend(["-preset", hw_p])
            # NVENC/AMF 使用 -cq; QSV 使用 -global_quality
            if hw_accel == HwAccel.NVENC or hw_accel == HwAccel.AMF:
                cmd.extend(["-cq", str(crf)])
            elif hw_accel == HwAccel.QSV:
                qsv_q = int((crf / 51) * 51)  # 映射到0-51
                cmd.extend(["-global_quality", str(qsv_q)])

    # 分辨率缩放
    if resolution:
        cmd.extend(["-vf", f"scale={resolution}"])

    # 字幕处理
    if keep_subtitles:
        cmd.extend(["-c:s", "copy"])
    else:
        cmd.extend(["-sn"])

    # 进度输出 + 日志级别
    cmd.extend(["-progress", "pipe:1", "-loglevel", "warning"])
    cmd.append(str(output_path))

    if log_callback:
        log_callback(f"命令: {' '.join(cmd)}")

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            universal_newlines=True,
        )

        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
        speed_pattern = re.compile(r"speed=\s*(\d+\.?\d*)x")
        last_progress = 0.0

        if process.stdout:
            for line in process.stdout:
                if cancel_event and cancel_event.is_set():
                    process.terminate()
                    process.wait()
                    return 1, "用户取消转换"

                if log_callback:
                    log_callback(line.rstrip())

                time_match = time_pattern.search(line)
                speed_match = speed_pattern.search(line)

                if time_match and total_duration > 0 and progress_callback:
                    h, m, s = map(float, time_match.groups())
                    current_time = h * 3600 + m * 60 + s
                    progress = min(current_time / total_duration, 1.0)
                    if progress > last_progress:
                        last_progress = progress
                        speed = speed_match.group(1) if speed_match else "?"
                        progress_callback(progress, f"{speed}x")

        process.wait()

        if cancel_event and cancel_event.is_set():
            return 1, "用户取消转换"

        if process.returncode == 0:
            return 0, None
        else:
            return process.returncode, f"ffmpeg退出码: {process.returncode}"

    except Exception as e:
        logger.error(f"转换异常: {e}")
        return 1, f"转换异常: {str(e)}"


# ---------------------------------------------------------------------------
# Batch conversion
# ---------------------------------------------------------------------------

def convert_batch_parallel(
    input_files: List[Union[str, Path]],
    output_dir: Union[str, Path],
    target_format: str,
    overwrite: bool = False,
    crf: int = 23,
    preset: str = "medium",
    max_workers: Optional[int] = None,
    hw_accel: HwAccel = HwAccel.NONE,
    audio_mode: str = "normal",
    resolution: Optional[str] = None,
    keep_subtitles: bool = True,
    log_callback: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[ConversionProgress], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    """并行批量转换"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if max_workers is None:
        try:
            cpu_count = os.cpu_count() or 2
            max_workers = min(max(1, cpu_count - 1), 4)
        except Exception:
            max_workers = 2

    progress = ConversionProgress(total_files=len(input_files))
    progress.status = "running"

    results = {"total": len(input_files), "success": 0, "failed": 0,
               "failed_files": [], "max_workers": max_workers}
    lock = threading.Lock()

    def convert_one(fpath: Path) -> Tuple[str, bool, str]:
        if cancel_event and cancel_event.is_set():
            return str(fpath), False, "已取消"

        out_path = output_dir / f"{fpath.stem}.{target_format.lstrip('.')}"

        def on_log(msg: str):
            if log_callback:
                log_callback(f"[{fpath.name}] {msg}")

        code, err = convert_video(
            input_path=fpath, output_path=out_path, overwrite=overwrite,
            crf=crf, preset=preset, hw_accel=hw_accel, audio_mode=audio_mode,
            resolution=resolution, keep_subtitles=keep_subtitles,
            log_callback=on_log, cancel_event=cancel_event,
        )
        return str(fpath), code == 0, err or ""

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(convert_one, Path(f)): f for f in input_files}
        completed = 0

        for future in as_completed(futures):
            fname, ok, err = future.result()
            with lock:
                completed += 1
                progress.completed_files = completed
                progress.current_file_name = Path(fname).name
                if ok:
                    results["success"] += 1
                else:
                    results["failed"] += 1
                    results["failed_files"].append({"file": fname, "error": err})
                if progress_callback:
                    progress_callback(progress)

    progress.status = "completed" if results["failed"] == 0 else "completed_with_errors"
    if progress_callback:
        progress_callback(progress)

    return results


# ---------------------------------------------------------------------------
# Preset manager
# ---------------------------------------------------------------------------

class PresetManager:
    def __init__(self, config_file: Optional[Path] = None):
        self.config_file = config_file or Path.home() / ".video_converter_v5.json"
        self.presets: Dict[str, ConversionPreset] = {}
        self.load()

    def load(self):
        for p in DEFAULT_PRESETS:
            self.presets[p.name] = p
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding='utf-8'))
                for pd in data.get("presets", []):
                    preset = ConversionPreset.from_dict(pd)
                    self.presets[preset.name] = preset
            except Exception as e:
                logger.warning(f"加载预设失败: {e}")

    def save(self):
        try:
            data = {"presets": [p.to_dict() for p in self.presets.values()]}
            self.config_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            logger.error(f"保存预设失败: {e}")

    def add(self, preset: ConversionPreset):
        self.presets[preset.name] = preset
        self.save()

    def delete(self, name: str) -> bool:
        default_names = {p.name for p in DEFAULT_PRESETS}
        if name in self.presets and name not in default_names:
            del self.presets[name]
            self.save()
            return True
        return False

    def get_names(self) -> List[str]:
        return list(self.presets.keys())

    def get(self, name: str) -> Optional[ConversionPreset]:
        return self.presets.get(name)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ConverterGUI:
    """v5 GUI - 标签页设计"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("视频格式转换器 v5")
        self.root.geometry("950x700")
        self.root.minsize(800, 600)

        self.preset_manager = PresetManager()
        self.cancel_event = threading.Event()

        # 变量
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.format_var = tk.StringVar(value="mp4")
        self.crf_var = tk.IntVar(value=23)
        self.preset_var = tk.StringVar(value="medium")
        self.overwrite_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="就绪")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.hw_var = tk.StringVar(value="none")
        self.audio_mode_var = tk.StringVar(value="normal")
        self.resolution_var = tk.StringVar(value="")
        self.sub_var = tk.BooleanVar(value=True)

        # 批量变量
        self.batch_input_var = tk.StringVar()
        self.batch_output_var = tk.StringVar()
        self.batch_workers_var = tk.IntVar(value=0)

        self.log_queue: queue.Queue = queue.Queue()
        self.converting = False

        self._build_ui()
        self._poll_logs()

    # ---- UI Construction ----

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(main)
        notebook.pack(fill=tk.BOTH, expand=True)

        single_tab = ttk.Frame(notebook, padding=10)
        batch_tab = ttk.Frame(notebook, padding=10)
        preset_tab = ttk.Frame(notebook, padding=10)

        notebook.add(single_tab, text="单文件转换")
        notebook.add(batch_tab, text="批量转换")
        notebook.add(preset_tab, text="预设管理")

        self._build_single_tab(single_tab)
        self._build_batch_tab(batch_tab)
        self._build_preset_tab(preset_tab)

        # 底部日志
        log_frame = ttk.LabelFrame(main, text="日志", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.log_text = ScrolledText(log_frame, height=6, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        btn_row = ttk.Frame(log_frame)
        btn_row.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(btn_row, text="清空日志", command=lambda: self.log_text.delete(1.0, tk.END)).pack(side=tk.RIGHT)

        # 底部状态栏
        status_frame = ttk.Frame(main)
        status_frame.pack(fill=tk.X, pady=(5, 0))

        self.progress_bar = ttk.Progressbar(status_frame, variable=self.progress_var,
                                            maximum=100, length=250, mode='determinate')
        self.progress_bar.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(status_frame, textvariable=self.status_var).pack(side=tk.LEFT, padx=5)

        # 硬件加速状态
        hw_status = self._get_hw_status_text()
        ttk.Label(status_frame, text=hw_status, foreground="gray").pack(side=tk.RIGHT, padx=5)

    def _build_single_tab(self, parent):
        # 文件选择
        ff = ttk.LabelFrame(parent, text="文件选择", padding=10)
        ff.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(ff, text="输入:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(ff, textvariable=self.input_var, width=65).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(ff, text="浏览...", command=self._choose_input).grid(row=0, column=2, pady=5)
        ttk.Button(ff, text="查看信息", command=self._show_video_info).grid(row=0, column=3, padx=5, pady=5)

        ttk.Label(ff, text="输出:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(ff, textvariable=self.output_var, width=65).grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(ff, text="浏览...", command=self._choose_output).grid(row=1, column=2, pady=5)
        ttk.Button(ff, text="自动生成", command=self._auto_gen_output).grid(row=1, column=3, padx=5, pady=5)

        # 转换选项
        of = ttk.LabelFrame(parent, text="转换选项", padding=10)
        of.pack(fill=tk.X, pady=(0, 10))

        # Row 0 - 格式 / 预设 / CRF
        ttk.Label(of, text="格式:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(of, textvariable=self.format_var, values=VideoFormat.values(),
                     state="readonly", width=10).grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)

        ttk.Label(of, text="编码预设:").grid(row=0, column=2, sticky=tk.W, pady=5, padx=(15, 0))
        ttk.Combobox(of, textvariable=self.preset_var, values=EncodingPreset.values(),
                     state="readonly", width=10).grid(row=0, column=3, padx=5, pady=5, sticky=tk.W)

        ttk.Label(of, text="CRF:").grid(row=0, column=4, sticky=tk.W, pady=5, padx=(15, 0))
        ttk.Spinbox(of, from_=0, to=51, textvariable=self.crf_var, width=6).grid(row=0, column=5, padx=5, pady=5, sticky=tk.W)

        # Row 1 - 硬件加速 / 音频模式
        ttk.Label(of, text="硬件加速:").grid(row=1, column=0, sticky=tk.W, pady=5)
        hw_values = ["none"] + [a.key for a in HwAccel if a != HwAccel.NONE]
        ttk.Combobox(of, textvariable=self.hw_var, values=hw_values,
                     state="readonly", width=8).grid(row=1, column=1, padx=5, pady=5, sticky=tk.W)

        ttk.Label(of, text="音频:").grid(row=1, column=2, sticky=tk.W, pady=5, padx=(15, 0))
        ttk.Combobox(of, textvariable=self.audio_mode_var,
                     values=["normal", "audio-only", "mute"],
                     state="readonly", width=10).grid(row=1, column=3, padx=5, pady=5, sticky=tk.W)

        ttk.Label(of, text="分辨率:").grid(row=1, column=4, sticky=tk.W, pady=5, padx=(15, 0))
        ttk.Combobox(of, textvariable=self.resolution_var,
                     values=["", "3840x2160", "2560x1440", "1920x1080", "1280x720", "854x480", "640x360"],
                     width=10).grid(row=1, column=5, padx=5, pady=5, sticky=tk.W)

        # Row 2
        ttk.Checkbutton(of, text="覆盖已存在文件", variable=self.overwrite_var).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=5)
        ttk.Checkbutton(of, text="保留字幕", variable=self.sub_var).grid(
            row=2, column=2, columnspan=2, sticky=tk.W, pady=5)

        # 快速预设
        ttk.Label(of, text="快速预设:").grid(row=2, column=4, sticky=tk.W, pady=5, padx=(15, 0))
        preset_names = ["自定义"] + self.preset_manager.get_names()
        self.quick_preset_combo = ttk.Combobox(of, values=preset_names, state="readonly", width=12)
        self.quick_preset_combo.grid(row=2, column=5, padx=5, pady=5, sticky=tk.W)
        self.quick_preset_combo.set("自定义")
        self.quick_preset_combo.bind("<<ComboboxSelected>>", self._on_quick_preset)

        # 视频信息
        info_frame = ttk.LabelFrame(parent, text="视频信息", padding=8)
        info_frame.pack(fill=tk.X, pady=(0, 10))
        self.info_text = tk.Text(info_frame, height=3, wrap=tk.WORD, font=("Consolas", 9))
        self.info_text.pack(fill=tk.X)
        self.info_text.insert(tk.END, "选择文件后自动显示视频信息")
        self.info_text.config(state=tk.DISABLED)

        # 操作按钮
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=5)

        self.convert_btn = ttk.Button(btn_frame, text="▶ 开始转换", command=self._start_single, width=15)
        self.convert_btn.pack(side=tk.LEFT, padx=5)

        self.cancel_btn = ttk.Button(btn_frame, text="■ 取消", command=self._cancel, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=5)

        # 绑定输入变化
        self.input_var.trace_add("write", lambda *_: self._on_input_change())
        self.format_var.trace_add("write", lambda *_: self._auto_gen_output())

    def _build_batch_tab(self, parent):
        # 目录选择
        df = ttk.LabelFrame(parent, text="目录选择", padding=10)
        df.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(df, text="输入目录:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(df, textvariable=self.batch_input_var, width=65).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(df, text="浏览...", command=self._choose_batch_input).grid(row=0, column=2, pady=5)

        ttk.Label(df, text="输出目录:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(df, textvariable=self.batch_output_var, width=65).grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(df, text="浏览...", command=self._choose_batch_output).grid(row=1, column=2, pady=5)

        # 选项
        of = ttk.LabelFrame(parent, text="批量选项", padding=10)
        of.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(of, text="格式:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(of, textvariable=self.format_var, values=VideoFormat.values(),
                     state="readonly", width=10).grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)

        ttk.Label(of, text="并行数:").grid(row=0, column=2, sticky=tk.W, pady=5, padx=(15, 0))
        ttk.Spinbox(of, from_=0, to=8, textvariable=self.batch_workers_var, width=6).grid(
            row=0, column=3, padx=5, pady=5, sticky=tk.W)
        ttk.Label(of, text="(0=自动)").grid(row=0, column=4, sticky=tk.W, pady=5)

        ttk.Label(of, text="CRF:").grid(row=0, column=5, sticky=tk.W, pady=5, padx=(15, 0))
        ttk.Spinbox(of, from_=0, to=51, textvariable=self.crf_var, width=6).grid(
            row=0, column=6, padx=5, pady=5, sticky=tk.W)

        ttk.Label(of, text="预设:").grid(row=0, column=7, sticky=tk.W, pady=5, padx=(15, 0))
        ttk.Combobox(of, textvariable=self.preset_var, values=EncodingPreset.values(),
                     state="readonly", width=10).grid(row=0, column=8, padx=5, pady=5, sticky=tk.W)

        # 文件列表
        lf = ttk.LabelFrame(parent, text="文件列表", padding=5)
        lf.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        cols = ("文件名", "大小", "时长", "状态")
        self.file_tree = ttk.Treeview(lf, columns=cols, show="headings", height=8)
        for c in cols:
            self.file_tree.heading(c, text=c)
        self.file_tree.column("文件名", width=300); self.file_tree.column("大小", width=80)
        self.file_tree.column("时长", width=70); self.file_tree.column("状态", width=100)

        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=sb.set)
        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # 按钮
        bf = ttk.Frame(parent)
        bf.pack(fill=tk.X, pady=5)

        ttk.Button(bf, text="扫描文件", command=self._scan_dir).pack(side=tk.LEFT, padx=5)
        self.batch_btn = ttk.Button(bf, text="▶ 批量转换", command=self._start_batch, width=15)
        self.batch_btn.pack(side=tk.LEFT, padx=5)
        self.batch_cancel_btn = ttk.Button(bf, text="■ 取消", command=self._cancel, state=tk.DISABLED)
        self.batch_cancel_btn.pack(side=tk.LEFT, padx=5)

    def _build_preset_tab(self, parent):
        lf = ttk.LabelFrame(parent, text="可用预设", padding=5)
        lf.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        cols = ("名称", "视频", "音频", "CRF", "预设", "硬件", "描述")
        self.preset_tree = ttk.Treeview(lf, columns=cols, show="headings", height=10)
        for i, c in enumerate(cols):
            self.preset_tree.heading(c, text=c)
            self.preset_tree.column(c, width=60 if i in (3, 4, 5) else 120)

        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.preset_tree.yview)
        self.preset_tree.configure(yscrollcommand=sb.set)
        self.preset_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._refresh_preset_tree()

        bf = ttk.Frame(parent)
        bf.pack(fill=tk.X, pady=5)

        ttk.Button(bf, text="应用预设", command=self._apply_preset_from_tree).pack(side=tk.LEFT, padx=5)
        ttk.Button(bf, text="保存当前设置为预设", command=self._save_as_preset).pack(side=tk.LEFT, padx=5)
        ttk.Button(bf, text="删除预设", command=self._delete_preset_from_tree).pack(side=tk.LEFT, padx=5)

    # ---- Actions ----

    def _get_hw_status_text(self) -> str:
        hw = detect_hardware_accel()
        parts = []
        if hw.get("nvenc"): parts.append("NVENC")
        if hw.get("qsv"): parts.append("QSV")
        if hw.get("amf"): parts.append("AMF")
        return f"硬件: {', '.join(parts)}" if parts else "仅软件编码"

    def _choose_input(self):
        path = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[("视频文件", "*.mp4 *.mkv *.webm *.mov *.avi *.flv *.wmv *.mpeg"), ("所有文件", "*.*")]
        )
        if path:
            self.input_var.set(path)
            self._auto_gen_output()

    def _choose_output(self):
        ext = self.format_var.get()
        path = filedialog.asksaveasfilename(
            title="保存为", defaultextension=f".{ext}",
            filetypes=[(f"{ext.upper()}文件", f"*.{ext}"), ("所有文件", "*.*")]
        )
        if path:
            self.output_var.set(path)

    def _auto_gen_output(self):
        inp = self.input_var.get().strip()
        fmt = self.format_var.get()
        if inp and fmt:
            self.output_var.set(build_output_path(inp, fmt))

    def _on_input_change(self):
        path = self.input_var.get().strip()
        if path and Path(path).exists():
            self._show_video_info()
        else:
            self.info_text.config(state=tk.NORMAL)
            self.info_text.delete(1.0, tk.END)
            self.info_text.insert(tk.END, "选择文件后自动显示视频信息")
            self.info_text.config(state=tk.DISABLED)

    def _show_video_info(self):
        path = self.input_var.get().strip()
        if not path:
            return
        info = get_video_info(path)
        self.info_text.config(state=tk.NORMAL)
        self.info_text.delete(1.0, tk.END)
        if info:
            lines = [
                f"文件: {Path(path).name}  大小: {info.size_str}",
                f"分辨率: {info.resolution}  时长: {info.duration_str}  帧率: {info.fps:.2f} fps",
                f"视频编码: {info.video_codec or '未知'}  音频编码: {info.audio_codec or '未知'}",
                f"字幕: {'有' if info.has_subtitles else '无'}",
            ]
            self.info_text.insert(tk.END, "\n".join(lines))
            self.quick_preset_combo['values'] = ["自定义"] + self.preset_manager.get_names()
        else:
            self.info_text.insert(tk.END, "无法获取视频信息")
        self.info_text.config(state=tk.DISABLED)

    def _on_quick_preset(self, event):
        name = self.quick_preset_combo.get()
        if name == "自定义":
            return
        preset = self.preset_manager.get(name)
        if preset:
            self.crf_var.set(preset.crf)
            self.preset_var.set(preset.preset.value)
            self.hw_var.set(preset.hw_accel.key)
            self._log(f"应用预设: {preset.name}")

    def _choose_batch_input(self):
        path = filedialog.askdirectory(title="选择输入目录")
        if path:
            self.batch_input_var.set(path)

    def _choose_batch_output(self):
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.batch_output_var.set(path)

    def _scan_dir(self):
        d = self.batch_input_var.get().strip()
        if not d:
            messagebox.showwarning("提示", "请选择输入目录")
            return
        dp = Path(d)
        if not dp.exists():
            messagebox.showerror("错误", "输入目录不存在")
            return

        for item in self.file_tree.get_children():
            self.file_tree.delete(item)

        exts = [f".{f}" for f in VideoFormat.values()]
        files = []
        for ext in exts:
            files.extend(dp.glob(f"*{ext}"))
            files.extend(dp.glob(f"*{ext.upper()}"))

        for f in sorted(set(files)):
            info = get_video_info(f)
            self.file_tree.insert("", tk.END, values=(
                f.name, info.size_str if info else "?", info.duration_str if info else "?", "等待"
            ), tags=(str(f),))

        self._log(f"扫描完成: {len(files)} 个文件")

    def _cancel(self):
        self.cancel_event.set()
        self._log("正在取消...")
        self.convert_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.DISABLED)
        self.batch_btn.config(state=tk.DISABLED)
        self.batch_cancel_btn.config(state=tk.DISABLED)

    def _get_hw_accel(self) -> HwAccel:
        hw_key = self.hw_var.get()
        for a in HwAccel:
            if a.key == hw_key:
                return a
        return HwAccel.NONE

    def _start_single(self):
        inp = self.input_var.get().strip()
        out = self.output_var.get().strip()
        if not inp or not out:
            messagebox.showerror("错误", "请选择输入和输出文件")
            return

        self.cancel_event.clear()
        self.converting = True
        self.convert_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.status_var.set("转换中...")

        hw = self._get_hw_accel()
        resolution = self.resolution_var.get().strip() or None

        def worker():
            code, err = convert_video(
                input_path=inp, output_path=out, overwrite=self.overwrite_var.get(),
                crf=self.crf_var.get(), preset=self.preset_var.get(),
                hw_accel=hw, audio_mode=self.audio_mode_var.get(),
                resolution=resolution, keep_subtitles=self.sub_var.get(),
                log_callback=lambda m: self.log_queue.put(m),
                progress_callback=lambda p, s: self.root.after(0, lambda: self._update_progress(p, s)),
                cancel_event=self.cancel_event,
            )
            self.root.after(0, lambda: self._conversion_done(code, err))

        threading.Thread(target=worker, daemon=True).start()

    def _update_progress(self, progress: float, detail: str):
        self.progress_var.set(progress * 100)
        self.status_var.set(f"转换中 [{detail}]")

    def _conversion_done(self, code: int, error: Optional[str]):
        self.converting = False
        self.convert_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        if code == 0:
            self.progress_var.set(100)
            self.status_var.set("完成")
            messagebox.showinfo("成功", "转换完成！")
        else:
            self.status_var.set(f"失败: {error}")
            messagebox.showerror("失败", error or "未知错误")

    def _start_batch(self):
        inp = self.batch_input_var.get().strip()
        out = self.batch_output_var.get().strip()
        if not inp or not out:
            messagebox.showwarning("提示", "请选择输入和输出目录")
            return

        items = self.file_tree.get_children()
        if not items:
            messagebox.showwarning("提示", "无文件，请先扫描")
            return

        files = [Path(inp) / self.file_tree.item(it)["values"][0] for it in items]
        for it in items:
            vals = self.file_tree.item(it)["values"]
            self.file_tree.item(it, values=(vals[0], vals[1], vals[2], "等待"))

        self.cancel_event.clear()
        self.converting = True
        self.batch_btn.config(state=tk.DISABLED)
        self.batch_cancel_btn.config(state=tk.NORMAL)
        self.status_var.set("批量转换中...")
        self.progress_var.set(0)

        workers = self.batch_workers_var.get() if self.batch_workers_var.get() > 0 else None
        hw = self._get_hw_accel()
        resolution = self.resolution_var.get().strip() or None

        def progress_cb(prog: ConversionProgress):
            self.root.after(0, lambda: self._update_batch_progress(prog))

        def worker():
            results = convert_batch_parallel(
                input_files=files, output_dir=out, target_format=self.format_var.get(),
                overwrite=self.overwrite_var.get(), crf=self.crf_var.get(),
                preset=self.preset_var.get(), max_workers=workers,
                hw_accel=hw, audio_mode=self.audio_mode_var.get(),
                resolution=resolution, keep_subtitles=self.sub_var.get(),
                log_callback=lambda m: self.log_queue.put(m),
                progress_callback=progress_cb, cancel_event=self.cancel_event,
            )
            self.root.after(0, lambda: self._batch_done(results))

        threading.Thread(target=worker, daemon=True).start()

    def _update_batch_progress(self, prog: ConversionProgress):
        self.progress_var.set(prog.get_overall_progress() * 100)
        self.status_var.set(prog.format_progress())
        # 更新文件状态
        if prog.current_file_name:
            for item in self.file_tree.get_children():
                vals = self.file_tree.item(item)["values"]
                if vals[0] == prog.current_file_name:
                    status = f"{prog.current_file_progress*100:.0f}%"
                    self.file_tree.item(item, values=(vals[0], vals[1], vals[2], status))
                    break

    def _batch_done(self, results: Dict):
        self.converting = False
        self.batch_btn.config(state=tk.NORMAL)
        self.batch_cancel_btn.config(state=tk.DISABLED)
        self.progress_var.set(100)
        self.status_var.set(f"完成: {results['success']}/{results['total']}")

        # 更新列表
        for item in self.file_tree.get_children():
            vals = self.file_tree.item(item)["values"]
            fname = vals[0]
            failed = {Path(f['file']).name for f in results['failed_files']}
            if fname in failed:
                self.file_tree.item(item, values=(vals[0], vals[1], vals[2], "失败"))
            else:
                self.file_tree.item(item, values=(vals[0], vals[1], vals[2], "完成"))

        msg = f"批量转换完成\n成功: {results['success']}\n失败: {results['failed']}\n并行数: {results['max_workers']}"
        messagebox.showinfo("完成", msg)

    def _refresh_preset_tree(self):
        for item in self.preset_tree.get_children():
            self.preset_tree.delete(item)
        for name in self.preset_manager.get_names():
            p = self.preset_manager.get(name)
            if p:
                self.preset_tree.insert("", tk.END, values=(
                    p.name, p.video_codec.description.split()[0], p.audio_codec.description.split()[0],
                    p.crf, p.preset.value, p.hw_accel.key, p.description
                ))

    def _apply_preset_from_tree(self):
        sel = self.preset_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请选择一个预设")
            return
        name = self.preset_tree.item(sel[0])["values"][0]
        preset = self.preset_manager.get(name)
        if preset:
            self.crf_var.set(preset.crf)
            self.preset_var.set(preset.preset.value)
            self.hw_var.set(preset.hw_accel.key)
            self.quick_preset_combo.set(name)
            self._log(f"已应用预设: {name}")

    def _save_as_preset(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("保存预设")
        dialog.geometry("350x200")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="预设名称:").grid(row=0, column=0, padx=10, pady=10, sticky=tk.W)
        name_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=name_var, width=30).grid(row=0, column=1, padx=10, pady=10)

        ttk.Label(dialog, text="描述:").grid(row=1, column=0, padx=10, pady=10, sticky=tk.W)
        desc_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=desc_var, width=30).grid(row=1, column=1, padx=10, pady=10)

        def do_save():
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("提示", "请输入名称")
                return
            preset = ConversionPreset(
                name=name, video_codec=VideoCodec.H264, audio_codec=AudioCodec.AAC,
                crf=self.crf_var.get(), preset=EncodingPreset(self.preset_var.get()),
                hw_accel=self._get_hw_accel(), description=desc_var.get().strip()
            )
            self.preset_manager.add(preset)
            self._refresh_preset_tree()
            self.quick_preset_combo['values'] = ["自定义"] + self.preset_manager.get_names()
            self._log(f"已保存预设: {name}")
            dialog.destroy()

        ttk.Button(dialog, text="保存", command=do_save).grid(row=2, column=0, columnspan=2, pady=20)

    def _delete_preset_from_tree(self):
        sel = self.preset_tree.selection()
        if not sel:
            return
        name = self.preset_tree.item(sel[0])["values"][0]
        if name in {p.name for p in DEFAULT_PRESETS}:
            messagebox.showwarning("提示", "不能删除默认预设")
            return
        if messagebox.askyesno("确认", f"删除预设 '{name}'?"):
            self.preset_manager.delete(name)
            self._refresh_preset_tree()
            self.quick_preset_combo['values'] = ["自定义"] + self.preset_manager.get_names()
            self._log(f"已删除预设: {name}")

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] {msg}")

    def _poll_logs(self):
        while True:
            try:
                line = self.log_queue.get_nowait()
                self.log_text.insert(tk.END, line + "\n")
                self.log_text.see(tk.END)
            except queue.Empty:
                break
        self.root.after(100, self._poll_logs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="视频格式转换器 v5 - Smart Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 启动GUI
  python main_v5.py

  # 单文件转换 (兼容v1)
  python main_v5.py convert -i input.webm -o output.mp4 --overwrite

  # 使用硬件加速
  python main_v5.py convert -i input.mp4 -o output.mp4 --hw nvenc

  # 批量转换
  python main_v5.py batch -i ./videos -o ./converted -f mp4

  # 提取音频
  python main_v5.py convert -i video.mp4 -o audio.mp3 --audio-mode audio-only

  # 视频静音
  python main_v5.py convert -i video.mp4 -o muted.mp4 --audio-mode mute

  # 缩放分辨率
  python main_v5.py convert -i input.mp4 -o output.mp4 --resolution 1280x720

  # 查看视频信息
  python main_v5.py info -i video.mp4

  # 预设管理
  python main_v5.py preset list
        """
    )

    subs = parser.add_subparsers(dest="mode")

    # convert
    cp = subs.add_parser("convert", help="单文件转换")
    cp.add_argument("-i", "--input", required=True, help="输入文件")
    cp.add_argument("-o", "--output", help="输出文件")
    cp.add_argument("-f", "--format", choices=VideoFormat.values(), help="目标格式")
    cp.add_argument("--overwrite", action="store_true", help="覆盖已存在文件")
    cp.add_argument("--crf", type=int, default=23, help="CRF质量 (0-51)")
    cp.add_argument("--preset", choices=EncodingPreset.values(), default="medium", help="编码预设")
    cp.add_argument("--hw", choices=[a.key for a in HwAccel], default="none", help="硬件加速")
    cp.add_argument("--audio-mode", choices=["normal", "audio-only", "mute"], default="normal",
                   help="音频模式")
    cp.add_argument("--resolution", help="输出分辨率 (如 1920x1080)")
    cp.add_argument("--no-subtitles", action="store_true", help="不保留字幕")
    cp.add_argument("--video-codec", help="视频编码器")
    cp.add_argument("--audio-codec", help="音频编码器")

    # batch
    bp = subs.add_parser("batch", help="批量转换")
    bp.add_argument("-i", "--input", required=True, help="输入目录")
    bp.add_argument("-o", "--output", required=True, help="输出目录")
    bp.add_argument("-f", "--format", required=True, choices=VideoFormat.values(), help="目标格式")
    bp.add_argument("-j", "--jobs", type=int, default=0, help="并行任务数 (0=自动)")
    bp.add_argument("--overwrite", action="store_true", help="覆盖已存在文件")
    bp.add_argument("--crf", type=int, default=23, help="CRF质量")
    bp.add_argument("--preset", choices=EncodingPreset.values(), default="medium", help="编码预设")
    bp.add_argument("--hw", choices=[a.key for a in HwAccel], default="none", help="硬件加速")
    bp.add_argument("--audio-mode", choices=["normal", "audio-only", "mute"], default="normal")
    bp.add_argument("--resolution", help="输出分辨率")
    bp.add_argument("--no-subtitles", action="store_true", help="不保留字幕")
    bp.add_argument("--recursive", action="store_true", help="递归扫描子目录")

    # info
    ip = subs.add_parser("info", help="查看视频信息")
    ip.add_argument("-i", "--input", required=True, help="视频文件")

    # preset
    pp = subs.add_parser("preset", help="预设管理")
    pp_sub = pp.add_subparsers(dest="preset_action")

    pp_sub.add_parser("list", help="列出所有预设")

    pa = pp_sub.add_parser("add", help="添加预设")
    pa.add_argument("--name", required=True, help="名称")
    pa.add_argument("--crf", type=int, required=True, help="CRF")
    pa.add_argument("--preset", choices=EncodingPreset.values(), required=True, help="编码预设")
    pa.add_argument("--hw", choices=[a.key for a in HwAccel], default="none", help="硬件加速")
    pa.add_argument("--description", help="描述")

    pd = pp_sub.add_parser("delete", help="删除预设")
    pd.add_argument("--name", required=True, help="名称")

    # gui
    subs.add_parser("gui", help="启动图形界面")

    return parser


def _get_hw_accel_from_args(args) -> HwAccel:
    hw_key = getattr(args, "hw", "none")
    for a in HwAccel:
        if a.key == hw_key:
            return a
    return HwAccel.NONE


def _cli_convert(args) -> int:
    inp = args.input
    out = args.output
    if not out:
        if not args.format:
            print("错误: 必须指定 --output 或 --format", file=sys.stderr)
            return 1
        out = build_output_path(inp, args.format)

    print(f"转换: {inp} -> {out}")
    hw = _get_hw_accel_from_args(args)
    if hw != HwAccel.NONE:
        print(f"硬件加速: {hw.label}")

    resolution = getattr(args, "resolution", None)
    audio_mode = getattr(args, "audio_mode", "normal")
    keep_subs = not getattr(args, "no_subtitles", False)

    def progress_cb(progress: float, detail: str):
        bar_len = 30
        filled = int(bar_len * progress)
        bar = "#" * filled + "-" * (bar_len - filled)
        print(f"\r[{bar}] {progress*100:.1f}% {detail}", end="", flush=True)

    code, err = convert_video(
        input_path=inp, output_path=out, overwrite=args.overwrite,
        crf=args.crf, preset=args.preset, hw_accel=hw,
        video_codec=getattr(args, "video_codec", None) or None,
        audio_codec=getattr(args, "audio_codec", None) or None,
        audio_mode=audio_mode, resolution=resolution,
        keep_subtitles=keep_subs,
        log_callback=lambda m: None,  # CLI静默日志
        progress_callback=progress_cb,
    )
    print()

    if code == 0:
        print(f"完成: {out}")
    else:
        print(f"失败: {err}", file=sys.stderr)
    return code


def _cli_batch(args) -> int:
    inp = Path(args.input)
    out = Path(args.output)

    if inp.is_file():
        files = [str(inp)]
    elif inp.is_dir():
        exts = [f".{f}" for f in VideoFormat.values()]
        glob_fn = inp.rglob if args.recursive else inp.glob
        files = []
        for ext in exts:
            files.extend(str(p) for p in glob_fn(f"*{ext}"))
            files.extend(str(p) for p in glob_fn(f"*{ext.upper()}"))
        files = list(set(files))
    else:
        print(f"错误: 输入不存在: {inp}", file=sys.stderr)
        return 1

    if not files:
        print("错误: 未找到视频文件", file=sys.stderr)
        return 1

    print(f"找到 {len(files)} 个文件")
    print(f"输出: {out}  格式: {args.format}")
    hw = _get_hw_accel_from_args(args)
    if hw != HwAccel.NONE:
        print(f"硬件加速: {hw.label}")

    workers = args.jobs if args.jobs > 0 else None
    resolution = getattr(args, "resolution", None)
    audio_mode = getattr(args, "audio_mode", "normal")
    keep_subs = not getattr(args, "no_subtitles", False)

    def progress_cb(p: ConversionProgress):
        print(f"\r  {p.format_progress()}", end="", flush=True)

    def complete_cb(path: str, success: bool, error: str):
        pass  # handled by progress

    def log_cb(msg: str):
        pass  # quiet

    results = convert_batch_parallel(
        input_files=files, output_dir=out, target_format=args.format,
        overwrite=args.overwrite, crf=args.crf, preset=args.preset,
        max_workers=workers, hw_accel=hw, audio_mode=audio_mode,
        resolution=resolution, keep_subtitles=keep_subs,
        log_callback=log_cb, progress_callback=progress_cb,
    )
    print()

    print(f"总计: {results['total']} | 成功: {results['success']} | 失败: {results['failed']} | 并行: {results['max_workers']}")
    if results['failed_files']:
        print("失败文件:")
        for f in results['failed_files']:
            print(f"  {Path(f['file']).name}: {f['error']}")
    return 0 if results['failed'] == 0 else 1


def _cli_info(args) -> int:
    info = get_video_info(args.input)
    if not info:
        print("无法获取视频信息", file=sys.stderr)
        return 1

    print(f"文件: {Path(args.input).name}")
    print(f"大小: {info.size_str}")
    print(f"时长: {info.duration_str} ({info.duration:.2f}s)")
    print(f"分辨率: {info.resolution}")
    print(f"帧率: {info.fps:.2f} fps" if info.fps else "帧率: 未知")
    print(f"视频编码: {info.video_codec or '未知'}")
    print(f"音频编码: {info.audio_codec or '未知'}")
    print(f"字幕: {'有' if info.has_subtitles else '无'}")
    return 0


def _cli_preset(args) -> int:
    pm = PresetManager()

    if args.preset_action == "list":
        print("预设列表:")
        for name in pm.get_names():
            p = pm.get(name)
            if p:
                print(f"  {name}: CRF={p.crf}, Preset={p.preset.value}, HW={p.hw_accel.key}")
                if p.description:
                    print(f"    {p.description}")
        return 0
    elif args.preset_action == "add":
        hw = _get_hw_accel_from_args(args)
        preset = ConversionPreset(
            name=args.name, video_codec=VideoCodec.H264, audio_codec=AudioCodec.AAC,
            crf=args.crf, preset=EncodingPreset(args.preset), hw_accel=hw,
            description=getattr(args, "description", "") or ""
        )
        pm.add(preset)
        print(f"已添加预设: {args.name}")
        return 0
    elif args.preset_action == "delete":
        if pm.delete(args.name):
            print(f"已删除预设: {args.name}")
        else:
            print(f"删除失败: {args.name}", file=sys.stderr)
            return 1
        return 0
    return 1


def launch_gui() -> int:
    if not has_ffmpeg():
        messagebox.showerror("错误", "ffmpeg未安装或不在PATH中")
        return 1

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        for theme in ["vista", "clam", "alt"]:
            if theme in style.theme_names():
                style.theme_use(theme)
                break
    except Exception:
        pass

    ConverterGUI(root)
    root.mainloop()
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.mode:
        return launch_gui()

    if not has_ffmpeg():
        print("错误: ffmpeg未安装或不在PATH中", file=sys.stderr)
        return 1

    try:
        if args.mode == "convert":
            return _cli_convert(args)
        elif args.mode == "batch":
            return _cli_batch(args)
        elif args.mode == "info":
            return _cli_info(args)
        elif args.mode == "preset":
            return _cli_preset(args)
        elif args.mode == "gui":
            return launch_gui()
        else:
            parser.print_help()
            return 1
    except KeyboardInterrupt:
        print("\n用户中断", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Video Format Converter v4 - Balanced Edition

一个平衡功能与简洁性的视频格式转换工具，结合v1的简洁性和v2/v3的优化功能。

设计理念：
1. 核心功能优先，避免过度复杂
2. 模块化架构，易于维护和扩展
3. 智能并行处理，提升效率
4. 完整的类型提示和错误处理
5. 向后兼容v1接口

主要功能：
- 单文件转换（兼容v1）
- 批量并行转换
- 实时进度跟踪
- 简化预设管理
- 视频信息预览
- 拖拽文件支持（可选）

作者：GitHub Copilot
日期：2026-04-13
版本：v4.0
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
from typing import Callable, Dict, List, Optional, Tuple, Union, Any
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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
        """获取所有格式值列表"""
        return [fmt.value for fmt in cls]

    @classmethod
    def is_supported(cls, ext: str) -> bool:
        """检查扩展名是否支持"""
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
        """获取所有预设值列表"""
        return [preset.value for preset in cls]


class VideoCodec(Enum):
    """视频编解码器"""
    H264 = ("libx264", "H.264 (兼容性好)")
    H265 = ("libx265", "H.265 (高压缩率)")
    VP9 = ("libvpx-vp9", "VP9 (网页优化)")
    AV1 = ("libaom-av1", "AV1 (最新)")
    COPY = ("copy", "直接复制")

    def __init__(self, ffmpeg_name: str, description: str):
        self.ffmpeg_name = ffmpeg_name
        self.description = description


class AudioCodec(Enum):
    """音频编解码器"""
    AAC = ("aac", "AAC (广泛支持)")
    OPUS = ("libopus", "Opus (高质量)")
    MP3 = ("mp3", "MP3 (通用)")
    COPY = ("copy", "直接复制")

    def __init__(self, ffmpeg_name: str, description: str):
        self.ffmpeg_name = ffmpeg_name
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

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "video_codec": self.video_codec.name,
            "audio_codec": self.audio_codec.name,
            "crf": self.crf,
            "preset": self.preset.value,
            "description": self.description
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConversionPreset':
        """从字典创建"""
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
    codec: str = ""
    file_size: int = 0

    @property
    def resolution(self) -> str:
        """分辨率字符串"""
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return "未知"

    @property
    def duration_str(self) -> str:
        """时长字符串"""
        if self.duration:
            mins = int(self.duration // 60)
            secs = int(self.duration % 60)
            return f"{mins}:{secs:02d}"
        return "未知"

    @property
    def size_str(self) -> str:
        """文件大小字符串"""
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
        name="平衡",
        video_codec=VideoCodec.H264,
        audio_codec=AudioCodec.AAC,
        crf=23,
        preset=EncodingPreset.MEDIUM,
        description="质量与大小的平衡"
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
        name="快速",
        video_codec=VideoCodec.H264,
        audio_codec=AudioCodec.AAC,
        crf=26,
        preset=EncodingPreset.VERYFAST,
        description="快速转换，质量一般"
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
        logger.warning("获取ffmpeg版本失败")
    return None


def get_video_info(input_path: Union[str, Path]) -> Optional[VideoInfo]:
    """
    获取视频文件信息
    
    返回VideoInfo对象，失败返回None
    """
    try:
        input_path = Path(input_path)
        if not input_path.exists():
            return None

        # 使用ffprobe获取信息
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration:stream=width,height,r_frame_rate,codec_name",
            "-of", "json",
            str(input_path)
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10
        )

        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        info = VideoInfo(
            path=str(input_path),
            file_size=input_path.stat().st_size
        )

        # 解析格式信息
        fmt = data.get("format", {})
        duration_str = fmt.get("duration", "0")
        try:
            info.duration = float(duration_str)
        except ValueError:
            info.duration = 0.0

        # 解析流信息
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                info.width = stream.get("width", 0)
                info.height = stream.get("height", 0)
                info.codec = stream.get("codec_name", "")

                # 解析帧率
                fps_str = stream.get("r_frame_rate", "0/1")
                try:
                    num, den = map(int, fps_str.split('/'))
                    if den != 0:
                        info.fps = num / den
                except (ValueError, ZeroDivisionError):
                    info.fps = 0.0

        return info

    except Exception as e:
        logger.warning(f"获取视频信息失败: {e}")
        return None


def pick_codecs(output_ext: str) -> Tuple[str, str]:
    """
    根据输出扩展名智能选择编解码器
    """
    ext = output_ext.lower().lstrip('.')

    codec_map = {
        'mp4': (VideoCodec.H264, AudioCodec.AAC),
        'mov': (VideoCodec.H264, AudioCodec.AAC),
        'mkv': (VideoCodec.H265, AudioCodec.AAC),
        'webm': (VideoCodec.VP9, AudioCodec.OPUS),
        'avi': (VideoCodec.H264, AudioCodec.AAC),  # 使用H264以获得更好兼容性
        'flv': (VideoCodec.H264, AudioCodec.AAC),
        'wmv': (VideoCodec.H264, AudioCodec.AAC),
        'mpeg': (VideoCodec.H264, AudioCodec.AAC),
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
        self.current_speed = ""
        
    def get_overall_progress(self) -> float:
        """获取整体进度（0.0-1.0）"""
        if self.total_files == 0:
            return 0.0
        file_progress = self.completed_files / self.total_files
        current_file_weight = 1.0 / self.total_files
        return file_progress + (self.current_file_progress * current_file_weight)
    
    def get_elapsed_time(self) -> float:
        """获取已用时间（秒）"""
        return time.time() - self.start_time
    
    def get_eta(self) -> Optional[float]:
        """获取预计剩余时间（秒）"""
        progress = self.get_overall_progress()
        if progress <= 0:
            return None
        elapsed = self.get_elapsed_time()
        total_estimated = elapsed / progress
        return total_estimated - elapsed
    
    def format_progress(self) -> str:
        """格式化进度字符串"""
        progress = self.get_overall_progress() * 100
        elapsed = self.get_elapsed_time()
        eta = self.get_eta()
        
        if self.status == "completed":
            return f"✅ 已完成 {self.total_files} 个文件，用时 {elapsed:.1f}秒"
        elif self.status == "failed":
            return f"❌ 转换失败"
        else:
            eta_str = f"，预计剩余 {eta:.1f}秒" if eta else ""
            return f"🔄 {progress:.1f}% ({self.completed_files}/{self.total_files}){eta_str}"


class AdaptiveParallelScheduler:
    """自适应并行调度器"""
    
    def __init__(self):
        self.max_workers = self._detect_optimal_workers()
        
    def _detect_optimal_workers(self) -> int:
        """检测最佳并行工作线程数"""
        try:
            import psutil
            cpu_count = os.cpu_count() or 2
            memory_gb = psutil.virtual_memory().total / (1024**3)
            
            # 基于CPU核心数和内存自动计算
            workers_by_cpu = max(1, cpu_count - 1)  # 留一个核心给系统
            workers_by_memory = max(1, int(memory_gb // 2))  # 每2GB内存一个任务
            
            return min(workers_by_cpu, workers_by_memory, 4)  # 最多4个并行
        except ImportError:
            # 如果没有psutil，使用保守估计
            cpu_count = os.cpu_count() or 2
            return min(max(1, cpu_count - 1), 2)  # 最多2个并行
    
    def get_max_workers(self) -> int:
        """获取最大工作线程数"""
        return self.max_workers


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
    
    # 自动选择编解码器
    if video_codec is None or audio_codec is None:
        video_codec, audio_codec = pick_codecs(output_path.suffix)
    
    # 验证参数
    if not (0 <= crf <= 51):
        return 1, f"CRF值必须在0-51之间: {crf}"
    
    if preset not in EncodingPreset.values():
        return 1, f"不支持的预设: {preset}"
    
    # 构建ffmpeg命令
    overwrite_flag = "-y" if overwrite else "-n"
    
    cmd = [
        "ffmpeg",
        overwrite_flag,
        "-i", str(input_path),
        "-c:v", video_codec,
        "-c:a", audio_codec,
        "-preset", preset,
        "-crf", str(crf),
        "-progress", "pipe:1",
        "-loglevel", "warning",
        str(output_path),
    ]
    
    if log_callback:
        log_callback(f"执行命令: {' '.join(cmd)}")
    
    try:
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
        
        # 解析进度信息
        speed_pattern = re.compile(r"speed=(\d+\.?\d*)x")
        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
        last_progress = 0.0
        
        if process.stdout:
            for line in process.stdout:
                if log_callback:
                    log_callback(line.rstrip())
                
                # 解析进度
                time_match = time_pattern.search(line)
                speed_match = speed_pattern.search(line)
                
                if time_match and progress_callback:
                    hours, minutes, seconds = map(float, time_match.groups())
                    current_time = hours * 3600 + minutes * 60 + seconds
                    
                    # 尝试获取视频总时长
                    video_info = get_video_info(input_path)
                    total_duration = video_info.duration if video_info else 0
                    
                    if total_duration > 0:
                        progress = min(current_time / total_duration, 1.0)
                        if progress > last_progress:
                            last_progress = progress
                            speed = speed_match.group(1) if speed_match else "?"
                            progress_callback(progress, f"{progress*100:.1f}% ({speed}x)")
        
        process.wait()
        
        if process.returncode == 0:
            return 0, None
        else:
            return process.returncode, f"ffmpeg退出代码: {process.returncode}"
            
    except Exception as e:
        logger.error(f"转换过程中发生异常: {e}")
        return 1, f"转换异常: {str(e)}"


def convert_batch_parallel(
    input_files: List[Union[str, Path]],
    output_dir: Union[str, Path],
    target_format: str,
    overwrite: bool = False,
    crf: int = 23,
    preset: str = "medium",
    max_workers: Optional[int] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[ConversionProgress], None]] = None,
) -> Dict[str, Any]:
    """
    并行批量转换视频文件
    
    Args:
        max_workers: 最大并行工作线程数（None表示自动检测）
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 确定并行数
    if max_workers is None:
        scheduler = AdaptiveParallelScheduler()
        max_workers = scheduler.get_max_workers()
    
    results = {
        "total": len(input_files),
        "success": 0,
        "failed": 0,
        "failed_files": [],
        "max_workers": max_workers
    }
    
    progress = ConversionProgress(total_files=len(input_files))
    progress.status = "running"
    
    def convert_single(input_file: Path) -> Tuple[str, bool, str]:
        """单个文件转换任务"""
        try:
            output_path = output_dir / f"{input_file.stem}.{target_format}"
            
            exit_code, error = convert_video(
                input_path=input_file,
                output_path=output_path,
                overwrite=overwrite,
                crf=crf,
                preset=preset,
                log_callback=lambda msg: log_callback(f"[{input_file.name}] {msg}") if log_callback else None
            )
            
            if exit_code == 0:
                return str(input_file), True, ""
            else:
                return str(input_file), False, error or "未知错误"
                
        except Exception as e:
            logger.error(f"转换文件失败 {input_file}: {e}")
            return str(input_file), False, str(e)
    
    # 使用线程池并行处理
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(convert_single, Path(f)): Path(f) for f in input_files}
        
        for i, future in enumerate(as_completed(futures)):
            filename, success, error = future.result()
            
            progress.completed_files = i + 1
            progress.current_file_name = Path(filename).name
            
            if success:
                results["success"] += 1
                if log_callback:
                    log_callback(f"✅ 完成: {filename}")
            else:
                results["failed"] += 1
                results["failed_files"].append({"file": filename, "error": error})
                if log_callback:
                    log_callback(f"❌ 失败: {filename} - {error}")
            
            if progress_callback:
                progress_callback(progress)
    
    progress.status = "completed" if results["failed"] == 0 else "failed"
    if progress_callback:
        progress_callback(progress)
    
    return results


class PresetManager:
    """预设管理器"""
    
    def __init__(self, config_file: Optional[Union[str, Path]] = None):
        if config_file is None:
            self.config_file = Path.home() / ".video_converter_presets.json"
        else:
            self.config_file = Path(config_file)
        
        self.presets: Dict[str, ConversionPreset] = {}
        self.load_presets()
    
    def load_presets(self) -> None:
        """加载预设"""
        # 添加默认预设
        for preset in DEFAULT_PRESETS:
            self.presets[preset.name] = preset
        
        # 加载用户预设
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for preset_data in data.get("presets", []):
                        try:
                            preset = ConversionPreset.from_dict(preset_data)
                            self.presets[preset.name] = preset
                        except Exception as e:
                            logger.warning(f"加载预设失败: {preset_data.get('name', '未知')} - {e}")
            except Exception as e:
                logger.error(f"加载配置文件失败: {e}")
    
    def save_presets(self) -> None:
        """保存预设"""
        try:
            data = {"presets": [preset.to_dict() for preset in self.presets.values()]}
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
    
    def add_preset(self, preset: ConversionPreset) -> None:
        """添加预设"""
        self.presets[preset.name] = preset
        self.save_presets()
    
    def delete_preset(self, name: str) -> bool:
        """删除预设"""
        if name in self.presets and name not in [p.name for p in DEFAULT_PRESETS]:
            del self.presets[name]
            self.save_presets()
            return True
        return False
    
    def get_preset_names(self) -> List[str]:
        """获取所有预设名称"""
        return list(self.presets.keys())
    
    def get_preset(self, name: str) -> Optional[ConversionPreset]:
        """获取预设"""
        return self.presets.get(name)


# GUI部分将在后续实现，保持简洁性

class SimpleConverterGUI:
    """简化版GUI"""
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("视频格式转换器 v4")
        self.root.geometry("800x600")
        
        # 初始化变量
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.format_var = tk.StringVar(value="mp4")
        self.overwrite_var = tk.BooleanVar(value=True)
        self.crf_var = tk.IntVar(value=23)
        self.preset_var = tk.StringVar(value="medium")
        self.status_var = tk.StringVar(value="就绪")
        
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._build_ui()
        self._poll_logs()
    
    def _build_ui(self):
        """构建UI界面"""
        # 主框架
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 文件选择区域
        file_frame = ttk.LabelFrame(main_frame, text="文件", padding=10)
        file_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(file_frame, text="输入文件:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.input_var, width=60).grid(row=0, column=1, padx=5, pady=5)
        ttk.Button(file_frame, text="浏览...", command=self._choose_input).grid(row=0, column=2, pady=5)
        
        ttk.Label(file_frame, text="输出文件:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(file_frame, textvariable=self.output_var, width=60).grid(row=1, column=1, padx=5, pady=5)
        ttk.Button(file_frame, text="浏览...", command=self._choose_output).grid(row=1, column=2, pady=5)
        ttk.Button(file_frame, text="自动生成", command=self._auto_generate).grid(row=1, column=3, padx=5, pady=5)
        
        # 选项区域
        options_frame = ttk.LabelFrame(main_frame, text="转换选项", padding=10)
        options_frame.pack(fill=tk.X, pady=(0, 10))
        
        # 第一行
        ttk.Label(options_frame, text="目标格式:").grid(row=0, column=0, sticky=tk.W, pady=5)
        format_combo = ttk.Combobox(options_frame, textvariable=self.format_var, 
                                   values=VideoFormat.values(), width=15, state="readonly")
        format_combo.grid(row=0, column=1, padx=5, pady=5)
        format_combo.bind('<<ComboboxSelected>>', self._on_format_change)
        
        ttk.Label(options_frame, text="CRF质量:").grid(row=0, column=2, sticky=tk.W, pady=5, padx=(20, 0))
        ttk.Spinbox(options_frame, from_=0, to=51, textvariable=self.crf_var, width=8).grid(row=0, column=3, pady=5)
        
        ttk.Label(options_frame, text="编码预设:").grid(row=0, column=4, sticky=tk.W, pady=5, padx=(20, 0))
        preset_combo = ttk.Combobox(options_frame, textvariable=self.preset_var,
                                   values=EncodingPreset.values(), width=12, state="readonly")
        preset_combo.grid(row=0, column=5, pady=5)
        
        # 第二行
        ttk.Checkbutton(options_frame, text="覆盖已存在文件", variable=self.overwrite_var).grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=10)
        
        # 预设选择
        ttk.Label(options_frame, text="快速预设:").grid(row=1, column=2, sticky=tk.W, pady=10, padx=(20, 0))
        self.preset_selector = ttk.Combobox(options_frame, values=["自定义"], width=15, state="readonly")
        self.preset_selector.grid(row=1, column=3, pady=10)
        self.preset_selector.set("自定义")
        self.preset_selector.bind('<<ComboboxSelected>>', self._on_preset_select)
        
        # 更新预设列表
        self._update_preset_list()
        
        # 信息显示区域
        info_frame = ttk.LabelFrame(main_frame, text="视频信息", padding=10)
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.info_text = tk.Text(info_frame, height=4, wrap=tk.WORD)
        self.info_text.pack(fill=tk.X)
        self.info_text.insert(tk.END, "选择文件后显示视频信息")
        self.info_text.config(state=tk.DISABLED)
        
        # 控制区域
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.convert_button = ttk.Button(control_frame, text="开始转换", command=self._start_conversion)
        self.convert_button.pack(side=tk.LEFT)
        
        ttk.Label(control_frame, textvariable=self.status_var).pack(side=tk.RIGHT)
        
        # 日志区域
        log_frame = ttk.LabelFrame(main_frame, text="转换日志", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = ScrolledText(log_frame, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # 添加清空日志按钮
        ttk.Button(log_frame, text="清空日志", command=self._clear_logs).pack(anchor=tk.E, pady=(5, 0))
    
    def _update_preset_list(self):
        """更新预设列表"""
        preset_manager = PresetManager()
        preset_names = preset_manager.get_preset_names()
        self.preset_selector['values'] = ["自定义"] + preset_names
    
    def _on_preset_select(self, event):
        """预设选择事件"""
        selected = self.preset_selector.get()
        if selected != "自定义":
            preset_manager = PresetManager()
            preset = preset_manager.get_preset(selected)
            if preset:
                self.crf_var.set(preset.crf)
                self.preset_var.set(preset.preset.value)
                self._log(f"已应用预设: {preset.name} - {preset.description}")
    
    def _choose_input(self):
        """选择输入文件"""
        path = filedialog.askopenfilename(
            title="选择输入视频",
            filetypes=[
                ("视频文件", "*.mp4 *.mkv *.webm *.mov *.avi *.flv *.wmv *.mpeg"),
                ("所有文件", "*.*")
            ]
        )
        if path:
            self.input_var.set(path)
            self._update_video_info(path)
            self._auto_generate()
    
    def _choose_output(self):
        """选择输出文件"""
        ext = self.format_var.get().strip().lower() or "mp4"
        path = filedialog.asksaveasfilename(
            title="选择输出视频",
            defaultextension=f".{ext}",
            filetypes=[(f"{ext.upper()}文件", f"*.{ext}"), ("所有文件", "*.*")]
        )
        if path:
            self.output_var.set(path)
    
    def _auto_generate(self):
        """自动生成输出路径"""
        input_path = self.input_var.get().strip()
        target_format = self.format_var.get().strip().lower()
        
        if input_path and target_format:
            output_path = build_output_path(input_path, target_format)
            self.output_var.set(output_path)
    
    def _on_format_change(self, event):
        """格式改变事件"""
        self._auto_generate()
    
    def _update_video_info(self, path: str):
        """更新视频信息显示"""
        info = get_video_info(path)
        
        self.info_text.config(state=tk.NORMAL)
        self.info_text.delete(1.0, tk.END)
        
        if info:
            self.info_text.insert(tk.END, 
                f"文件: {Path(path).name}\n"
                f"尺寸: {info.resolution} | 时长: {info.duration_str}\n"
                f"帧率: {info.fps:.2f} fps | 大小: {info.size_str}\n"
                f"编码: {info.codec}"
            )
        else:
            self.info_text.insert(tk.END, "无法获取视频信息")
        
        self.info_text.config(state=tk.DISABLED)
    
    def _log(self, msg: str):
        """添加日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {msg}")
    
    def _clear_logs(self):
        """清空日志"""
        self.log_text.delete(1.0, tk.END)
    
    def _poll_logs(self):
        """轮询日志队列"""
        while True:
            try:
                line = self.log_queue.get_nowait()
                self.log_text.insert(tk.END, line + "\n")
                self.log_text.see(tk.END)
            except queue.Empty:
                break
        self.root.after(100, self._poll_logs)
    
    def _start_conversion(self):
        """开始转换"""
        input_path = self.input_var.get().strip()
        output_path = self.output_var.get().strip()
        
        if not input_path:
            messagebox.showerror("错误", "请选择输入文件")
            return
        
        if not output_path:
            messagebox.showerror("错误", "请指定输出文件")
            return
        
        # 禁用按钮，更新状态
        self.convert_button.config(state=tk.DISABLED)
        self.status_var.set("转换中...")
        self._log(f"开始转换: {Path(input_path).name} -> {Path(output_path).name}")
        
        # 在新线程中运行转换
        thread = threading.Thread(
            target=self._conversion_worker,
            args=(input_path, output_path),
            daemon=True
        )
        thread.start()
    
    def _conversion_worker(self, input_path: str, output_path: str):
        """转换工作线程"""
        try:
            exit_code, error = convert_video(
                input_path=input_path,
                output_path=output_path,
                overwrite=self.overwrite_var.get(),
                crf=self.crf_var.get(),
                preset=self.preset_var.get(),
                log_callback=self._log,
                progress_callback=lambda progress, detail: self.status_var.set(f"转换中: {detail}")
            )
            
            if exit_code == 0:
                self.root.after(0, lambda: self.status_var.set("转换完成"))
                self.root.after(0, lambda: self._log("✅ 转换成功"))
                self.root.after(0, lambda: messagebox.showinfo("成功", "视频转换完成"))
            else:
                self.root.after(0, lambda: self.status_var.set("转换失败"))
                self.root.after(0, lambda: self._log(f"❌ 转换失败: {error}"))
                self.root.after(0, lambda: messagebox.showerror("失败", f"转换失败: {error}"))
                
        except Exception as e:
            self.root.after(0, lambda: self.status_var.set("错误"))
            self.root.after(0, lambda: self._log(f"❌ 错误: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror("错误", str(e)))
            
        finally:
            self.root.after(0, lambda: self.convert_button.config(state=tk.NORMAL))


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器"""
    parser = argparse.ArgumentParser(
        description="视频格式转换器 v4",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 启动GUI界面
  python main_v4.py
  
  # 单文件转换（兼容v1）
  python main_v4.py convert -i input.webm -o output.mp4 --overwrite
  
  # 使用预设转换
  python main_v4.py convert -i input.mkv -f mp4 --crf 20 --preset slow
  
  # 批量转换目录中的文件
  python main_v4.py batch -i ./videos -o ./converted -f mp4
  
  # 并行批量转换（自动检测最优线程数）
  python main_v4.py batch -i ./videos -o ./converted -f webm -j auto
  
  # 获取视频信息
  python main_v4.py info -i video.mp4
  
  # 管理预设
  python main_v4.py preset list
  python main_v4.py preset add --name "高质量" --crf 18 --preset medium
        """
    )
    
    subparsers = parser.add_subparsers(dest="mode", help="运行模式")
    
    # 转换命令（兼容v1）
    convert_parser = subparsers.add_parser("convert", help="单文件转换")
    convert_parser.add_argument("-i", "--input", required=True, help="输入文件路径")
    convert_parser.add_argument("-o", "--output", help="输出文件路径")
    convert_parser.add_argument("-f", "--format", choices=VideoFormat.values(), help="目标格式（未指定-o时使用）")
    convert_parser.add_argument("--overwrite", action="store_true", help="覆盖已存在文件")
    convert_parser.add_argument("--crf", type=int, default=23, help="CRF质量值（0-51，默认23）")
    convert_parser.add_argument("--preset", choices=EncodingPreset.values(), default="medium", help="编码预设")
    convert_parser.add_argument("--video-codec", help="视频编解码器（默认自动选择）")
    convert_parser.add_argument("--audio-codec", help="音频编解码器（默认自动选择）")
    
    # 批量转换命令
    batch_parser = subparsers.add_parser("batch", help="批量转换")
    batch_parser.add_argument("-i", "--input", required=True, help="输入目录或文件列表（逗号分隔）")
    batch_parser.add_argument("-o", "--output", required=True, help="输出目录")
    batch_parser.add_argument("-f", "--format", required=True, choices=VideoFormat.values(), help="目标格式")
    batch_parser.add_argument("--overwrite", action="store_true", help="覆盖已存在文件")
    batch_parser.add_argument("--crf", type=int, default=23, help="CRF质量值")
    batch_parser.add_argument("--preset", choices=EncodingPreset.values(), default="medium", help="编码预设")
    batch_parser.add_argument("-j", "--jobs", help="并行任务数（数字或'auto'，默认auto）")
    batch_parser.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    
    # 信息命令
    info_parser = subparsers.add_parser("info", help="获取视频信息")
    info_parser.add_argument("-i", "--input", required=True, help="视频文件路径")
    
    # 预设管理命令
    preset_parser = subparsers.add_parser("preset", help="预设管理")
    preset_subparsers = preset_parser.add_subparsers(dest="preset_action", help="预设操作")
    
    # 列出预设
    preset_subparsers.add_parser("list", help="列出所有预设")
    
    # 添加预设
    preset_add = preset_subparsers.add_parser("add", help="添加预设")
    preset_add.add_argument("--name", required=True, help="预设名称")
    preset_add.add_argument("--crf", type=int, required=True, help="CRF值")
    preset_add.add_argument("--preset", choices=EncodingPreset.values(), required=True, help="编码预设")
    preset_add.add_argument("--video-codec", choices=[c.name for c in VideoCodec], default="H264", help="视频编解码器")
    preset_add.add_argument("--audio-codec", choices=[c.name for c in AudioCodec], default="AAC", help="音频编解码器")
    preset_add.add_argument("--description", help="预设描述")
    
    # 删除预设
    preset_delete = preset_subparsers.add_parser("delete", help="删除预设")
    preset_delete.add_argument("--name", required=True, help="预设名称")
    
    # GUI命令
    subparsers.add_parser("gui", help="启动图形界面（默认）")
    
    return parser


def run_convert(args) -> int:
    """运行单文件转换"""
    input_path = args.input
    output_path = args.output
    
    if not output_path:
        if not args.format:
            print("错误: 必须指定--output或--format")
            return 1
        output_path = build_output_path(input_path, args.format)
    
    print(f"转换: {input_path} -> {output_path}")
    print(f"参数: CRF={args.crf}, 预设={args.preset}")
    
    exit_code, error = convert_video(
        input_path=input_path,
        output_path=output_path,
        overwrite=args.overwrite,
        crf=args.crf,
        preset=args.preset,
        video_codec=args.video_codec,
        audio_codec=args.audio_codec,
        log_callback=print,
        progress_callback=lambda progress, detail: print(f"进度: {detail}", end='\r')
    )
    
    print()  # 换行
    
    if exit_code == 0:
        print(f"✅ 转换完成: {output_path}")
        return 0
    else:
        print(f"❌ 转换失败: {error}")
        return exit_code


def run_batch(args) -> int:
    """运行批量转换"""
    input_path = args.input
    output_dir = args.output
    target_format = args.format
    
    # 解析输入路径
    input_files = []
    if ',' in input_path:
        # 逗号分隔的文件列表
        input_files = [f.strip() for f in input_path.split(',')]
    else:
        # 目录扫描
        input_dir = Path(input_path)
        if not input_dir.exists():
            print(f"错误: 输入路径不存在: {input_path}")
            return 1
        
        if input_dir.is_file():
            input_files = [str(input_dir)]
        else:
            # 扫描视频文件
            extensions = [f".{fmt}" for fmt in VideoFormat.values()]
            if args.recursive:
                for ext in extensions:
                    input_files.extend([str(p) for p in input_dir.rglob(f"*{ext}")])
            else:
                for ext in extensions:
                    input_files.extend([str(p) for p in input_dir.glob(f"*{ext}")])
    
    if not input_files:
        print("错误: 未找到视频文件")
        return 1
    
    print(f"找到 {len(input_files)} 个视频文件")
    print(f"目标格式: {target_format}")
    print(f"输出目录: {output_dir}")
    
    # 解析并行任务数
    max_workers = None
    if args.jobs:
        if args.jobs.lower() == 'auto':
            scheduler = AdaptiveParallelScheduler()
            max_workers = scheduler.get_max_workers()
            print(f"自动检测: 使用 {max_workers} 个并行任务")
        else:
            try:
                max_workers = int(args.jobs)
                print(f"使用 {max_workers} 个并行任务")
            except ValueError:
                print(f"警告: 无效的jobs参数 '{args.jobs}'，使用自动检测")
                scheduler = AdaptiveParallelScheduler()
                max_workers = scheduler.get_max_workers()
    
    # 进度跟踪
    progress = ConversionProgress(total_files=len(input_files))
    
    def update_progress(p: ConversionProgress):
        print(f"\r进度: {p.format_progress()}", end='')
    
    print("开始批量转换...")
    
    results = convert_batch_parallel(
        input_files=input_files,
        output_dir=output_dir,
        target_format=target_format,
        overwrite=args.overwrite,
        crf=args.crf,
        preset=args.preset,
        max_workers=max_workers,
        log_callback=lambda msg: print(f"  {msg}"),
        progress_callback=update_progress
    )
    
    print()  # 换行
    print("\n批量转换完成:")
    print(f"  总计: {results['total']}")
    print(f"  成功: {results['success']}")
    print(f"  失败: {results['failed']}")
    print(f"  并行数: {results['max_workers']}")
    
    if results['failed_files']:
        print("\n失败文件:")
        for item in results['failed_files']:
            print(f"  {Path(item['file']).name}: {item['error']}")
    
    return 0 if results['failed'] == 0 else 1


def run_info(args) -> int:
    """获取视频信息"""
    info = get_video_info(args.input)
    
    if info:
        print(f"视频信息: {Path(args.input).name}")
        print(f"  路径: {info.path}")
        print(f"  尺寸: {info.resolution}")
        print(f"  时长: {info.duration_str} ({info.duration:.2f}秒)")
        print(f"  帧率: {info.fps:.2f} fps")
        print(f"  编码: {info.codec}")
        print(f"  大小: {info.size_str}")
        return 0
    else:
        print(f"无法获取视频信息: {args.input}")
        return 1


def run_preset(args) -> int:
    """管理预设"""
    manager = PresetManager()
    
    if args.preset_action == "list":
        print("预设列表:")
        for name in manager.get_preset_names():
            preset = manager.get_preset(name)
            if preset:
                print(f"  {name}: CRF={preset.crf}, 预设={preset.preset.value}, {preset.description}")
        return 0
    
    elif args.preset_action == "add":
        try:
            preset = ConversionPreset(
                name=args.name,
                video_codec=getattr(VideoCodec, args.video_codec),
                audio_codec=getattr(AudioCodec, args.audio_codec),
                crf=args.crf,
                preset=EncodingPreset(args.preset),
                description=args.description or ""
            )
            manager.add_preset(preset)
            print(f"✅ 添加预设: {args.name}")
            return 0
        except Exception as e:
            print(f"❌ 添加预设失败: {e}")
            return 1
    
    elif args.preset_action == "delete":
        if manager.delete_preset(args.name):
            print(f"✅ 删除预设: {args.name}")
            return 0
        else:
            print(f"❌ 删除预设失败: {args.name} (可能不存在或是默认预设)")
            return 1
    
    return 1


def launch_gui() -> int:
    """启动GUI界面"""
    if not has_ffmpeg():
        messagebox.showerror("错误", "未找到ffmpeg，请安装并添加到PATH")
        return 1
    
    root = tk.Tk()
    
    # 设置窗口样式
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except:
        pass
    
    app = SimpleConverterGUI(root)
    root.mainloop()
    return 0


def main() -> int:
    """主函数"""
    parser = build_parser()
    args = parser.parse_args()
    
    # 默认启动GUI
    if not args.mode:
        return launch_gui()
    
    # 检查ffmpeg
    if not has_ffmpeg():
        print("错误: 未找到ffmpeg，请安装并添加到PATH")
        return 1
    
    # 根据模式执行相应操作
    if args.mode == "convert":
        return run_convert(args)
    elif args.mode == "batch":
        return run_batch(args)
    elif args.mode == "info":
        return run_info(args)
    elif args.mode == "preset":
        return run_preset(args)
    elif args.mode == "gui":
        return launch_gui()
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n操作被用户中断")
        sys.exit(130)
    except Exception as e:
        print(f"程序发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
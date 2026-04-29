#!/usr/bin/env python3
"""
Video Format Converter v6 - Compression Edition

v6 核心新增 -- 视频文件大小压缩:
1. 两遍编码 (Two-Pass VBR) -- 精确控制输出文件大小
2. CRF 快速压缩 -- 快速估算压缩
3. 分辨率缩放压缩 -- 降低分辨率减小体积
4. 预估压缩后大小 -- 转换前预览预估结果
5. 压缩预设 -- 一键压缩到指定大小/比例
6. 压缩进度 -- 两遍编码显示阶段进度

继承 v5 全部功能:
- 硬件加速 (NVENC/QSV/AMF)
- 智能流复制 / 可取消转换
- 字幕/音频模式/分辨率缩放
- 并行批量 + 标签页 GUI
- 向后兼容 v1 CLI 接口
"""

import argparse, json, logging, os, queue, re, shutil, subprocess, sys, tempfile
import threading, time, tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union, Any
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ===========================================================================
# Enums
# ===========================================================================

class VideoFormat(Enum):
    MP4="mp4"; MKV="mkv"; WEBM="webm"; MOV="mov"
    AVI="avi"; FLV="flv"; WMV="wmv"; MPEG="mpeg"
    @classmethod
    def values(cls)->List[str]: return [f.value for f in cls]
    @classmethod
    def is_supported(cls,ext:str)->bool: return ext.lower().lstrip('.') in cls.values()

class EncodingPreset(Enum):
    ULTRAFAST="ultrafast"; SUPERFAST="superfast"; VERYFAST="veryfast"
    FASTER="faster"; FAST="fast"; MEDIUM="medium"
    SLOW="slow"; SLOWER="slower"; VERYSLOW="veryslow"
    @classmethod
    def values(cls)->List[str]: return [p.value for p in cls]

class VideoCodec(Enum):
    H264=("libx264","H.264 (兼容性好)")
    H265=("libx265","H.265 (高压缩率)")
    VP9=("libvpx-vp9","VP9 (网页优化)")
    AV1=("libaom-av1","AV1 (最新)")
    COPY=("copy","流复制")
    def __init__(self,fn:str,desc:str): self.ffmpeg_name=fn; self.description=desc

class AudioCodec(Enum):
    AAC=("aac","AAC (广泛支持)")
    OPUS=("libopus","Opus (高质量)")
    MP3=("mp3","MP3 (通用)")
    COPY=("copy","流复制")
    def __init__(self,fn:str,desc:str): self.ffmpeg_name=fn; self.description=desc

class HwAccel(Enum):
    NONE=("none","软件编码"); NVENC=("nvenc","NVIDIA NVENC")
    QSV=("qsv","Intel QuickSync"); AMF=("amf","AMD AMF")
    def __init__(self,key:str,label:str): self.key=key; self.label=label

class CompressMethod(Enum):
    TWO_PASS=("two-pass","两遍编码(精确)")
    CRF=("crf","CRF快速(粗略)")
    RESIZE=("resize","缩放分辨率")
    AUTO=("auto","自动选择")
    def __init__(self,key:str,label:str): self.key=key; self.label=label

# ===========================================================================
# Data classes
# ===========================================================================

@dataclass
class ConversionPreset:
    name:str; video_codec:VideoCodec; audio_codec:AudioCodec
    crf:int; preset:EncodingPreset; hw_accel:HwAccel=HwAccel.NONE
    description:str=""
    def to_dict(self)->Dict:
        return {"name":self.name,"video_codec":self.video_codec.name,
                "audio_codec":self.audio_codec.name,"crf":self.crf,
                "preset":self.preset.value,"hw_accel":self.hw_accel.key,
                "description":self.description}
    @classmethod
    def from_dict(cls,d:Dict)->'ConversionPreset':
        hw=HwAccel.NONE
        for a in HwAccel:
            if a.key==d.get("hw_accel","none"): hw=a; break
        return cls(name=d["name"],video_codec=getattr(VideoCodec,d["video_codec"]),
                   audio_codec=getattr(AudioCodec,d["audio_codec"]),crf=d["crf"],
                   preset=EncodingPreset(d["preset"]),hw_accel=hw,
                   description=d.get("description",""))

@dataclass
class CompressPreset:
    name:str; method:CompressMethod
    target_size_mb:Optional[float]=None
    target_ratio:Optional[float]=None
    crf:Optional[int]=None
    resolution:Optional[str]=None
    description:str=""
    def to_dict(self)->Dict:
        return {"name":self.name,"method":self.method.key,
                "target_size_mb":self.target_size_mb,"target_ratio":self.target_ratio,
                "crf":self.crf,"resolution":self.resolution,"description":self.description}
    @classmethod
    def from_dict(cls,d:Dict)->'CompressPreset':
        method=CompressMethod.AUTO
        for m in CompressMethod:
            if m.key==d.get("method","auto"): method=m; break
        return cls(name=d["name"],method=method,target_size_mb=d.get("target_size_mb"),
                   target_ratio=d.get("target_ratio"),crf=d.get("crf"),
                   resolution=d.get("resolution"),description=d.get("description",""))

@dataclass
class VideoInfo:
    path:str; duration:float=0.0; width:int=0; height:int=0; fps:float=0.0
    video_codec:str=""; audio_codec:str=""
    video_bitrate:int=0; audio_bitrate:int=0
    file_size:int=0; has_subtitles:bool=False
    @property
    def resolution(self)->str:
        return f"{self.width}x{self.height}" if self.width else "未知"
    @property
    def duration_str(self)->str:
        if self.duration: m,s=divmod(int(self.duration),60); return f"{m}:{s:02d}"
        return "未知"
    @property
    def size_str(self)->str:
        size=self.file_size
        for u in['B','KB','MB','GB']:
            if size<1024: return f"{size:.1f} {u}"
            size/=1024
        return f"{size:.1f} TB"
    @property
    def size_mb(self)->float: return self.file_size/(1024*1024)

class ConversionProgress:
    def __init__(self,total_files:int=1):
        self.total_files=total_files; self.completed_files=0
        self.current_file_progress=0.0; self.current_file_name=""
        self.current_file_detail=""; self.current_pass=1; self.total_passes=1
        self.start_time=time.time(); self.status="pending"
        self._lock=threading.Lock()
    def get_overall_progress(self)->float:
        if self.total_files==0: return 0.0
        return self.completed_files/self.total_files+self.current_file_progress/self.total_files
    def get_eta(self)->Optional[float]:
        p=self.get_overall_progress()
        if p<=0: return None
        elapsed=time.time()-self.start_time
        return elapsed/p-elapsed
    def format_progress(self)->str:
        pct=self.get_overall_progress()*100; elapsed=time.time()-self.start_time
        if self.status=="completed": return f"完成 {self.total_files} 个文件，用时 {elapsed:.1f}s"
        eta=self.get_eta()
        eta_str=f"，预计剩余 {eta:.1f}s" if eta else ""
        detail=f" [{self.current_file_detail}]" if self.current_file_detail else ""
        pass_str=f" (第{self.current_pass}/{self.total_passes}遍)" if self.total_passes>1 else ""
        return f"{pct:.1f}% ({self.completed_files}/{self.total_files}){pass_str}{detail}{eta_str}"

# ===========================================================================
# Default presets
# ===========================================================================

DEFAULT_PRESETS=[
    ConversionPreset("高质量",VideoCodec.H264,AudioCodec.AAC,18,EncodingPreset.MEDIUM,description="高质量，文件较大"),
    ConversionPreset("平衡",VideoCodec.H264,AudioCodec.AAC,23,EncodingPreset.MEDIUM,description="质量与大小平衡"),
    ConversionPreset("小文件",VideoCodec.H265,AudioCodec.AAC,28,EncodingPreset.SLOW,description="文件小，质量可接受"),
    ConversionPreset("快速",VideoCodec.H264,AudioCodec.AAC,26,EncodingPreset.VERYFAST,description="快速转换"),
    ConversionPreset("仅复制",VideoCodec.COPY,AudioCodec.COPY,0,EncodingPreset.MEDIUM,description="不重新编码"),
]

DEFAULT_COMPRESS_PRESETS=[
    CompressPreset("压缩到50%",CompressMethod.AUTO,target_ratio=0.5,description="压缩到原始大小的50%"),
    CompressPreset("压缩到30%",CompressMethod.AUTO,target_ratio=0.3,description="大幅压缩到30%"),
    CompressPreset("压缩到100MB",CompressMethod.TWO_PASS,target_size_mb=100.0,description="精确压缩到约100MB"),
    CompressPreset("压缩到50MB",CompressMethod.TWO_PASS,target_size_mb=50.0,description="精确压缩到约50MB"),
    CompressPreset("CRF=30",CompressMethod.CRF,crf=30,description="CRF=30快速压缩"),
    CompressPreset("720p压缩",CompressMethod.RESIZE,resolution="1280x720",target_ratio=0.5,description="缩放720p+压缩"),
]

# ===========================================================================
# Utility
# ===========================================================================

def has_ffmpeg()->bool: return shutil.which("ffmpeg") is not None

_hw_cache:Optional[Dict[str,bool]]=None

def detect_hardware_accel()->Dict[str,bool]:
    global _hw_cache
    if _hw_cache is not None: return _hw_cache
    available={"nvenc":False,"qsv":False,"amf":False}
    if not has_ffmpeg(): _hw_cache=available; return available
    try:
        r=subprocess.run(["ffmpeg","-encoders"],capture_output=True,text=True,timeout=10)
        if r.returncode==0:
            e=r.stdout
            available["nvenc"]="h264_nvenc" in e
            available["qsv"]="h264_qsv" in e
            available["amf"]="h264_amf" in e
    except Exception as ex: logger.debug(f"HW检测失败:{ex}")
    _hw_cache=available; return available

def get_hw_encoder_name(hw:HwAccel,codec:VideoCodec)->Optional[str]:
    if hw==HwAccel.NONE or codec==VideoCodec.COPY: return None
    m={HwAccel.NVENC:{VideoCodec.H264:"h264_nvenc",VideoCodec.H265:"hevc_nvenc"},
       HwAccel.QSV:{VideoCodec.H264:"h264_qsv",VideoCodec.H265:"hevc_qsv"},
       HwAccel.AMF:{VideoCodec.H264:"h264_amf",VideoCodec.H265:"hevc_amf"}}
    return m.get(hw,{}).get(codec)

def get_video_info(input_path:Union[str,Path])->Optional[VideoInfo]:
    try:
        ip=Path(input_path)
        if not ip.exists(): return None
        cmd=["ffprobe","-v","error","-show_entries",
             "format=duration,bit_rate:stream=width,height,r_frame_rate,codec_name,codec_type,bit_rate",
             "-of","json",str(ip)]
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        if r.returncode!=0: return None
        data=json.loads(r.stdout)
        info=VideoInfo(path=str(ip),file_size=ip.stat().st_size)
        fmt=data.get("format",{})
        try: info.duration=float(fmt.get("duration",0))
        except ValueError: pass
        try: info.video_bitrate=int(fmt.get("bit_rate",0))
        except (ValueError,TypeError): pass
        for s in data.get("streams",[]):
            ct=s.get("codec_type")
            if ct=="video":
                info.width=s.get("width",0); info.height=s.get("height",0)
                info.video_codec=s.get("codec_name","")
                fps_str=s.get("r_frame_rate","0/1")
                try:
                    num,den=map(int,fps_str.split('/'))
                    if den!=0: info.fps=num/den
                except (ValueError,ZeroDivisionError): pass
                try:
                    br=s.get("bit_rate",0)
                    if br: info.video_bitrate=int(br)
                except (ValueError,TypeError): pass
            elif ct=="audio":
                info.audio_codec=s.get("codec_name","")
                try:
                    br=s.get("bit_rate",0)
                    if br: info.audio_bitrate=int(br)
                except (ValueError,TypeError): pass
            elif ct=="subtitle": info.has_subtitles=True
        return info
    except Exception as e:
        logger.debug(f"获取视频信息失败:{e}"); return None

def pick_codecs(output_ext:str)->Tuple[VideoCodec,AudioCodec]:
    ext=output_ext.lower().lstrip('.')
    m={'mp4':(VideoCodec.H264,AudioCodec.AAC),'mov':(VideoCodec.H264,AudioCodec.AAC),
       'mkv':(VideoCodec.H265,AudioCodec.AAC),'webm':(VideoCodec.VP9,AudioCodec.OPUS),
       'avi':(VideoCodec.H264,AudioCodec.AAC),'flv':(VideoCodec.H264,AudioCodec.AAC),
       'wmv':(VideoCodec.H264,AudioCodec.AAC),'mpeg':(VideoCodec.H264,AudioCodec.AAC)}
    return m.get(ext,(VideoCodec.H264,AudioCodec.AAC))

def can_stream_copy(src_codec:str,dst_format:str,codec_type:str="video")->bool:
    compat={"mp4":{"video":["h264","mpeg4","hevc","h265"],"audio":["aac","mp3"]},
            "mkv":{"video":["h264","hevc","h265","vp9","av1","mpeg4"],"audio":["aac","mp3","opus","vorbis","flac"]},
            "webm":{"video":["vp9","vp8","av1"],"audio":["opus","vorbis"]},
            "mov":{"video":["h264","hevc","h265","mpeg4"],"audio":["aac","mp3"]},
            "avi":{"video":["h264","mpeg4"],"audio":["mp3","aac"]},
            "flv":{"video":["h264","flv"],"audio":["aac","mp3"]}}
    allowed=compat.get(dst_format.lower(),{}).get(codec_type,[])
    return src_codec.lower() in allowed

def build_output_path(input_path:str,target_format:str,suffix:str="")->str:
    p=Path(input_path); fmt=target_format.lower().lstrip('.')
    stem=f"{p.stem}_{suffix}" if suffix else p.stem
    return str(p.with_name(f"{stem}.{fmt}"))

# ===========================================================================
# v6 新增: 压缩核心函数
# ===========================================================================

def calculate_target_bitrate(duration_sec:float,target_size_bytes:int,
                              audio_bitrate_bps:int=128000,
                              container_overhead_pct:float=0.02)->int:
    """计算达到目标文件大小所需的视频比特率 (bps)"""
    if duration_sec<=0: return 1000000
    effective=target_size_bytes*(1-container_overhead_pct)
    audio_size=(audio_bitrate_bps/8)*duration_sec
    video_size=effective-audio_size
    if video_size<=0: return 100000
    bitrate=int((video_size*8)/duration_sec)
    return max(100000,min(bitrate,50000000))

def estimate_compressed_size(info:VideoInfo,crf:int=23,
                              resolution:Optional[str]=None)->Dict[str,Any]:
    """预估压缩后文件大小 (启发式算法)"""
    if info.file_size<=0 or info.duration<=0:
        return {"estimated_mb":0,"estimated_ratio":0,"recommended_bitrate":0,"confidence":"low"}
    orig_bitrate=info.video_bitrate if info.video_bitrate>0 else info.file_size*8/info.duration
    crf_factor=2**((23-crf)/6)
    resolution_factor=1.0
    if resolution:
        try:
            w,h=map(int,resolution.lower().split('x'))
            orig_px=info.width*info.height; new_px=w*h
            if orig_px>0: resolution_factor=new_px/orig_px
        except (ValueError,AttributeError): pass
    est_bitrate=orig_bitrate*crf_factor*resolution_factor
    est_bytes=(est_bitrate/8)*info.duration
    est_bytes+=(128000/8)*info.duration
    est_bytes*=1.02
    est_mb=est_bytes/(1024*1024)
    return {"estimated_mb":round(est_mb,1),
            "estimated_ratio":round(est_mb/info.size_mb,2) if info.size_mb>0 else 0,
            "recommended_bitrate":int(est_bitrate),
            "confidence":"medium" if info.video_bitrate>0 else "low"}

def _run_ffmpeg_with_progress(cmd:List[str],total_duration:float,
                               progress_cb:Optional[Callable[[float,str],None]],
                               log_cb:Optional[Callable[[str],None]],
                               cancel_event:Optional[threading.Event])->int:
    """运行ffmpeg并解析进度"""
    try:
        proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                              text=True,encoding="utf-8",errors="replace",bufsize=1,universal_newlines=True)
        time_pat=re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
        speed_pat=re.compile(r"speed=\s*(\d+\.?\d*)x")
        last=0.0
        if proc.stdout:
            for line in proc.stdout:
                if cancel_event and cancel_event.is_set():
                    proc.terminate(); proc.wait(); return 1
                if log_cb: log_cb(line.rstrip())
                tm=time_pat.search(line)
                sm=speed_pat.search(line)
                if tm and total_duration>0 and progress_cb:
                    h,m,s=map(float,tm.groups())
                    cur=h*3600+m*60+s
                    p=min(cur/total_duration,1.0)
                    if p>last: last=p; speed=sm.group(1) if sm else "?"; progress_cb(p,f"{speed}x")
        proc.wait(); return proc.returncode
    except Exception as e:
        logger.error(f"ffmpeg异常:{e}"); return 1

def convert_video(input_path:Union[str,Path],output_path:Union[str,Path],
                  overwrite:bool=False,crf:int=23,preset:str="medium",
                  video_codec:Optional[str]=None,audio_codec:Optional[str]=None,
                  hw_accel:HwAccel=HwAccel.NONE,audio_mode:str="normal",
                  resolution:Optional[str]=None,keep_subtitles:bool=True,
                  log_callback:Optional[Callable[[str],None]]=None,
                  progress_callback:Optional[Callable[[float,str],None]]=None,
                  cancel_event:Optional[threading.Event]=None)->Tuple[int,Optional[str]]:
    """转换视频 (可取消), 返回 (exit_code, error_message)"""
    if not has_ffmpeg(): return 1,"ffmpeg未安装"
    ip=Path(input_path); op=Path(output_path)
    if not ip.exists(): return 1,f"输入文件不存在:{ip}"
    if op.exists() and not overwrite: return 1,f"输出文件已存在:{op}"
    vi=get_video_info(ip); total_dur=vi.duration if vi else 0.0
    if video_codec is None or audio_codec is None:
        avc,aac=pick_codecs(op.suffix)
        video_codec=video_codec or avc.ffmpeg_name
        audio_codec=audio_codec or aac.ffmpeg_name
    owf="-y" if overwrite else "-n"
    cmd=["ffmpeg",owf]
    if hw_accel!=HwAccel.NONE:
        hdm={HwAccel.NVENC:"cuda",HwAccel.QSV:"qsv",HwAccel.AMF:"d3d11va"}
        cmd.extend(["-hwaccel",hdm.get(hw_accel,"cuda")])
    cmd.extend(["-i",str(ip)])
    if audio_mode=="audio-only": cmd.extend(["-vn"])
    elif audio_mode=="mute": cmd.extend(["-an"])
    if hw_accel!=HwAccel.NONE and video_codec not in("copy",None):
        hwn=get_hw_encoder_name(hw_accel,VideoCodec.H264)
        cmd.extend(["-c:v",hwn if hwn else video_codec])
    else: cmd.extend(["-c:v",video_codec or "libx264"])
    if audio_mode!="mute": cmd.extend(["-c:a",audio_codec or "aac"])
    if video_codec!="copy":
        if hw_accel==HwAccel.NONE:
            cmd.extend(["-preset",preset,"-crf",str(crf)])
        else:
            hpm={"ultrafast":"fast","superfast":"fast","veryfast":"fast","faster":"medium",
                 "fast":"medium","medium":"medium","slow":"slow","slower":"slow","veryslow":"slow"}
            cmd.extend(["-preset",hpm.get(preset,"medium")])
            if hw_accel in(HwAccel.NVENC,HwAccel.AMF): cmd.extend(["-cq",str(crf)])
            elif hw_accel==HwAccel.QSV: cmd.extend(["-global_quality",str(int((crf/51)*51))])
    if resolution: cmd.extend(["-vf",f"scale={resolution}"])
    if keep_subtitles: cmd.extend(["-c:s","copy"])
    else: cmd.extend(["-sn"])
    cmd.extend(["-progress","pipe:1","-loglevel","warning",str(op)])
    if log_callback: log_callback(f"命令:{' '.join(cmd)}")
    ec=_run_ffmpeg_with_progress(cmd,total_dur,progress_callback,log_callback,cancel_event)
    if cancel_event and cancel_event.is_set(): return 1,"用户取消"
    return (0,None) if ec==0 else (ec,f"退出码:{ec}")

def _compress_two_pass(ip:Path,op:Path,info:VideoInfo,target_size_bytes:int,
                        audio_bitrate:int,vcodec:str,acodec:str,preset:str,
                        resolution:Optional[str],hw_accel:HwAccel,overwrite:bool,
                        log_cb:Optional[Callable[[str],None]],
                        progress_cb:Optional[Callable[[float,str,int,int],None]],
                        cancel_ev:Optional[threading.Event])->Tuple[int,Optional[str]]:
    """两遍编码实现"""
    tvb=calculate_target_bitrate(info.duration,target_size_bytes,audio_bitrate)
    if log_cb: log_cb(f"目标视频比特率:{tvb/1000:.0f}kbps")
    tlog=tempfile.NamedTemporaryFile(suffix=".log",delete=False)
    tlog_path=tlog.name; tlog.close()
    owf="-y" if overwrite else "-n"
    try:
        if progress_cb: progress_cb(0.0,"第一遍:分析视频",1,2)
        p1c=["ffmpeg",owf,"-i",str(ip),"-c:v",vcodec,"-b:v",str(tvb),"-preset",preset,
             "-pass","1","-passlogfile",tlog_path,"-an"]
        if resolution: p1c.extend(["-vf",f"scale={resolution}"])
        if os.name=="nt": p1c.extend(["-f","null","NUL"])
        else: p1c.extend(["-f","null","/dev/null"])
        p1c.extend(["-progress","pipe:1","-loglevel","warning"])
        if log_cb: log_cb(f"第一遍:{' '.join(p1c)}")
        ec=_run_ffmpeg_with_progress(p1c,info.duration,
            lambda p,s: progress_cb(p*0.45,f"第一遍:{s}",1,2) if progress_cb else None,
            log_cb,cancel_ev)
        if ec!=0: return ec,f"第一遍失败(退出码:{ec})"
        if cancel_ev and cancel_ev.is_set(): return 1,"用户取消"
        if progress_cb: progress_cb(0.45,"第二遍:编码输出",2,2)
        p2c=["ffmpeg",owf,"-i",str(ip),"-c:v",vcodec,"-b:v",str(tvb),"-preset",preset,
             "-pass","2","-passlogfile",tlog_path,"-c:a",acodec,"-b:a",str(audio_bitrate)]
        if resolution: p2c.extend(["-vf",f"scale={resolution}"])
        p2c.extend(["-progress","pipe:1","-loglevel","warning",str(op)])
        if log_cb: log_cb(f"第二遍:{' '.join(p2c)}")
        ec=_run_ffmpeg_with_progress(p2c,info.duration,
            lambda p,s: progress_cb(0.45+p*0.55,f"第二遍:{s}",2,2) if progress_cb else None,
            log_cb,cancel_ev)
        if ec==0 and progress_cb: progress_cb(1.0,"压缩完成",2,2)
        if ec==0 and op.exists():
            asz=op.stat().st_size/(1024*1024)
            if log_cb: log_cb(f"实际输出:{asz:.1f}MB (目标:{target_size_bytes/(1024*1024):.1f}MB)")
        return (0,None) if ec==0 else (ec,f"两遍编码失败(退出码:{ec})")
    finally:
        try:
            os.unlink(tlog_path)
            for x in[".mbtree",".c2",".cutree"]:
                p=Path(tlog_path+x)
                if p.exists(): p.unlink()
        except OSError: pass

def compress_video(input_path:Union[str,Path],output_path:Union[str,Path],
                   target_size_mb:Optional[float]=None,target_ratio:Optional[float]=None,
                   method:CompressMethod=CompressMethod.AUTO,crf:int=28,preset:str="medium",
                   resolution:Optional[str]=None,video_codec:Optional[str]=None,
                   audio_codec:Optional[str]=None,hw_accel:HwAccel=HwAccel.NONE,
                   audio_bitrate:int=128000,overwrite:bool=False,
                   log_callback:Optional[Callable[[str],None]]=None,
                   progress_callback:Optional[Callable[[float,str,int,int],None]]=None,
                   cancel_event:Optional[threading.Event]=None)->Tuple[int,Optional[str]]:
    """压缩视频 (v6核心), progress_callback: (progress, detail, cur_pass, total_passes)"""
    if not has_ffmpeg(): return 1,"ffmpeg未安装"
    ip=Path(input_path); op=Path(output_path)
    if not ip.exists(): return 1,f"输入文件不存在:{ip}"
    if op.exists() and not overwrite: return 1,f"输出文件已存在:{op}"
    info=get_video_info(ip)
    if not info or info.duration<=0: return 1,"无法获取视频信息"
    if target_ratio is not None and target_size_mb is None: target_size_mb=info.size_mb*target_ratio
    if target_size_mb is None and target_ratio is None: target_size_mb=info.size_mb*0.5; target_ratio=0.5
    tsz=int(target_size_mb*1024*1024)
    if log_callback:
        log_callback(f"原始:{info.size_str}({info.size_mb:.1f}MB)")
        log_callback(f"目标:{target_size_mb:.1f}MB | 时长:{info.duration_str} | 分辨率:{info.resolution}")
    if video_codec is None or audio_codec is None:
        avc,aac=pick_codecs(op.suffix)
        video_codec=video_codec or avc.ffmpeg_name; audio_codec=audio_codec or aac.ffmpeg_name
    if method==CompressMethod.AUTO:
        method=CompressMethod.CRF if (target_size_mb/info.size_mb>=0.8) else CompressMethod.TWO_PASS
    if log_callback: log_callback(f"压缩方法:{method.label}")
    if method==CompressMethod.TWO_PASS:
        return _compress_two_pass(ip,op,info,tsz,audio_bitrate,video_codec,audio_codec,
                                   preset,resolution,hw_accel,overwrite,log_callback,
                                   progress_callback,cancel_event)
    elif method==CompressMethod.CRF:
        if progress_callback: progress_callback(0.0,"CRF快速压缩",1,1)
        return convert_video(input_path=ip,output_path=op,overwrite=overwrite,crf=crf,preset=preset,
                             video_codec=video_codec,audio_codec=audio_codec,hw_accel=hw_accel,
                             resolution=resolution,log_callback=log_callback,
                             progress_callback=lambda p,s: progress_callback(p,s,1,1) if progress_callback else None,
                             cancel_event=cancel_event)
    elif method==CompressMethod.RESIZE:
        if resolution is None:
            rm={2160:"1920x1080",1440:"1920x1080",1080:"1280x720",720:"854x480",480:"640x360"}
            for hk,res in rm.items():
                if info.height>=hk: resolution=res; break
            if resolution is None: resolution="1280x720"
        if log_callback: log_callback(f"缩放:{info.resolution}->{resolution}")
        if progress_callback: progress_callback(0.0,"缩放+CRF压缩",1,1)
        return convert_video(input_path=ip,output_path=op,overwrite=overwrite,crf=crf,preset=preset,
                             video_codec=video_codec,audio_codec=audio_codec,hw_accel=hw_accel,
                             resolution=resolution,log_callback=log_callback,
                             progress_callback=lambda p,s: progress_callback(p,s,1,1) if progress_callback else None,
                             cancel_event=cancel_event)
    return 1,f"未知压缩方法:{method}"

# ===========================================================================
# Batch conversion
# ===========================================================================

def convert_batch_parallel(input_files:List[Union[str,Path]],output_dir:Union[str,Path],
                            target_format:str,overwrite:bool=False,crf:int=23,preset:str="medium",
                            max_workers:Optional[int]=None,hw_accel:HwAccel=HwAccel.NONE,
                            audio_mode:str="normal",resolution:Optional[str]=None,
                            keep_subtitles:bool=True,
                            log_callback:Optional[Callable[[str],None]]=None,
                            progress_callback:Optional[Callable[[ConversionProgress],None]]=None,
                            cancel_event:Optional[threading.Event]=None)->Dict[str,Any]:
    """并行批量转换"""
    od=Path(output_dir); od.mkdir(parents=True,exist_ok=True)
    if max_workers is None:
        try: cpu=os.cpu_count() or 2; max_workers=min(max(1,cpu-1),4)
        except: max_workers=2
    prog=ConversionProgress(total_files=len(input_files)); prog.status="running"
    results={"total":len(input_files),"success":0,"failed":0,"failed_files":[],"max_workers":max_workers}
    lock=threading.Lock()
    def cvt_one(fp:Path)->Tuple[str,bool,str]:
        if cancel_event and cancel_event.is_set(): return str(fp),False,"已取消"
        outp=od/f"{fp.stem}.{target_format.lstrip('.')}"
        def ol(msg:str):
            if log_callback: log_callback(f"[{fp.name}]{msg}")
        ec,err=convert_video(input_path=fp,output_path=outp,overwrite=overwrite,crf=crf,preset=preset,
                              hw_accel=hw_accel,audio_mode=audio_mode,resolution=resolution,
                              keep_subtitles=keep_subtitles,log_callback=ol,cancel_event=cancel_event)
        return str(fp),ec==0,err or ""
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures={ex.submit(cvt_one,Path(f)):f for f in input_files}; completed=0
        for future in as_completed(futures):
            fn,ok,err=future.result()
            with lock:
                completed+=1; prog.completed_files=completed
                prog.current_file_name=Path(fn).name
                if ok: results["success"]+=1
                else: results["failed"]+=1; results["failed_files"].append({"file":fn,"error":err})
                if progress_callback: progress_callback(prog)
    prog.status="completed" if results["failed"]==0 else "completed_with_errors"
    if progress_callback: progress_callback(prog)
    return results

# ===========================================================================
# Preset manager
# ===========================================================================

class PresetManager:
    def __init__(self,config_file:Optional[Path]=None):
        self.config_file=config_file or Path.home()/".video_converter_v6.json"
        self.presets:Dict[str,ConversionPreset]={}
        self.compress_presets:Dict[str,CompressPreset]={}
        self.load()
    def load(self):
        for p in DEFAULT_PRESETS: self.presets[p.name]=p
        for cp in DEFAULT_COMPRESS_PRESETS: self.compress_presets[cp.name]=cp
        if self.config_file.exists():
            try:
                data=json.loads(self.config_file.read_text(encoding='utf-8'))
                for pd in data.get("presets",[]):
                    p=ConversionPreset.from_dict(pd); self.presets[p.name]=p
                for cpd in data.get("compress_presets",[]):
                    cp=CompressPreset.from_dict(cpd); self.compress_presets[cp.name]=cp
            except Exception as e: logger.warning(f"加载预设失败:{e}")
    def save(self):
        try:
            data={"presets":[p.to_dict() for p in self.presets.values()],
                  "compress_presets":[cp.to_dict() for cp in self.compress_presets.values()]}
            self.config_file.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
        except Exception as e: logger.error(f"保存预设失败:{e}")
    def get_names(self)->List[str]: return list(self.presets.keys())
    def get_compress_names(self)->List[str]: return list(self.compress_presets.keys())
    def get(self,name:str)->Optional[ConversionPreset]: return self.presets.get(name)
    def get_compress(self,name:str)->Optional[CompressPreset]: return self.compress_presets.get(name)
    def add(self,p:ConversionPreset): self.presets[p.name]=p; self.save()
    def add_compress(self,cp:CompressPreset): self.compress_presets[cp.name]=cp; self.save()
    def delete(self,name:str)->bool:
        dn={p.name for p in DEFAULT_PRESETS}
        if name in self.presets and name not in dn: del self.presets[name]; self.save(); return True
        return False

# ===========================================================================
# GUI
# ===========================================================================

class ConverterGUI:
    """v6 GUI - 4个标签页: 单文件/批量/压缩/预设"""
    def __init__(self,root:tk.Tk):
        self.root=root
        self.root.title("视频格式转换器 v6 (压缩版)")
        self.root.geometry("1000x750"); self.root.minsize(900,650)
        self.pm=PresetManager(); self.cancel_event=threading.Event()
        # 变量
        self.input_var=tk.StringVar(); self.output_var=tk.StringVar()
        self.format_var=tk.StringVar(value="mp4"); self.crf_var=tk.IntVar(value=23)
        self.preset_var=tk.StringVar(value="medium"); self.overwrite_var=tk.BooleanVar(value=True)
        self.status_var=tk.StringVar(value="就绪"); self.progress_var=tk.DoubleVar(value=0.0)
        self.hw_var=tk.StringVar(value="none"); self.audio_mode_var=tk.StringVar(value="normal")
        self.resolution_var=tk.StringVar(value=""); self.sub_var=tk.BooleanVar(value=True)
        # 批量
        self.batch_input_var=tk.StringVar(); self.batch_output_var=tk.StringVar()
        self.batch_workers_var=tk.IntVar(value=0)
        # 压缩
        self.compress_method_var=tk.StringVar(value="auto")
        self.compress_target_mb_var=tk.StringVar(value="")
        self.compress_ratio_var=tk.StringVar(value="")
        self.compress_audio_br_var=tk.IntVar(value=128)
        self.compress_est_var=tk.StringVar(value="")
        self.log_queue:queue.Queue=queue.Queue(); self.converting=False
        self._build_ui(); self._poll_logs()
    # ---- UI ----
    def _build_ui(self):
        main=ttk.Frame(self.root,padding=10); main.pack(fill=tk.BOTH,expand=True)
        nb=ttk.Notebook(main); nb.pack(fill=tk.BOTH,expand=True)
        t1=ttk.Frame(nb,padding=10); t2=ttk.Frame(nb,padding=10)
        t3=ttk.Frame(nb,padding=10); t4=ttk.Frame(nb,padding=10)
        nb.add(t1,text="单文件转换"); nb.add(t2,text="批量转换")
        nb.add(t3,text="视频压缩 ⭐"); nb.add(t4,text="预设管理")
        self._build_single_tab(t1); self._build_batch_tab(t2)
        self._build_compress_tab(t3); self._build_preset_tab(t4)
        # 底部日志
        lf=ttk.LabelFrame(main,text="日志",padding=5)
        lf.pack(fill=tk.BOTH,expand=True,pady=(10,0))
        self.log_text=ScrolledText(lf,height=5,wrap=tk.WORD,font=("Consolas",9))
        self.log_text.pack(fill=tk.BOTH,expand=True)
        ttk.Button(lf,text="清空",command=lambda:self.log_text.delete(1.0,tk.END)).pack(anchor=tk.E,pady=(3,0))
        # 状态栏
        sf=ttk.Frame(main); sf.pack(fill=tk.X,pady=(5,0))
        self.progress_bar=ttk.Progressbar(sf,variable=self.progress_var,maximum=100,mode='determinate')
        self.progress_bar.pack(side=tk.LEFT,padx=(0,10))
        ttk.Label(sf,textvariable=self.status_var).pack(side=tk.LEFT,padx=5)
        hw=detect_hardware_accel(); parts=[]
        if hw.get("nvenc"): parts.append("NVENC")
        if hw.get("qsv"): parts.append("QSV")
        if hw.get("amf"): parts.append("AMF")
        hws=f"硬件:{', '.join(parts)}" if parts else "仅软件"
        ttk.Label(sf,text=hws,foreground="gray").pack(side=tk.RIGHT,padx=5)

    def _build_single_tab(self,p):
        ff=ttk.LabelFrame(p,text="文件选择",padding=10); ff.pack(fill=tk.X,pady=(0,10))
        ttk.Label(ff,text="输入:").grid(row=0,column=0,sticky=tk.W,pady=5)
        ttk.Entry(ff,textvariable=self.input_var,width=60).grid(row=0,column=1,padx=5,pady=5)
        ttk.Button(ff,text="浏览...",command=self._choose_input).grid(row=0,column=2,pady=5)
        ttk.Button(ff,text="查看信息",command=self._show_video_info).grid(row=0,column=3,padx=5,pady=5)
        ttk.Label(ff,text="输出:").grid(row=1,column=0,sticky=tk.W,pady=5)
        ttk.Entry(ff,textvariable=self.output_var,width=60).grid(row=1,column=1,padx=5,pady=5)
        ttk.Button(ff,text="浏览...",command=self._choose_output).grid(row=1,column=2,pady=5)
        ttk.Button(ff,text="自动生成",command=self._auto_gen_output).grid(row=1,column=3,padx=5,pady=5)
        of=ttk.LabelFrame(p,text="转换选项",padding=10); of.pack(fill=tk.X,pady=(0,10))
        ttk.Label(of,text="格式:").grid(row=0,column=0,sticky=tk.W,pady=5)
        ttk.Combobox(of,textvariable=self.format_var,values=VideoFormat.values(),state="readonly",width=10).grid(row=0,column=1,padx=5,pady=5,sticky=tk.W)
        ttk.Label(of,text="编码预设:").grid(row=0,column=2,sticky=tk.W,pady=5,padx=(15,0))
        ttk.Combobox(of,textvariable=self.preset_var,values=EncodingPreset.values(),state="readonly",width=10).grid(row=0,column=3,padx=5,pady=5,sticky=tk.W)
        ttk.Label(of,text="CRF:").grid(row=0,column=4,sticky=tk.W,pady=5,padx=(15,0))
        ttk.Spinbox(of,from_=0,to=51,textvariable=self.crf_var,width=6).grid(row=0,column=5,padx=5,pady=5,sticky=tk.W)
        ttk.Label(of,text="硬件加速:").grid(row=1,column=0,sticky=tk.W,pady=5)
        hwv=["none",*(a.key for a in HwAccel if a!=HwAccel.NONE)]
        ttk.Combobox(of,textvariable=self.hw_var,values=hwv,state="readonly",width=8).grid(row=1,column=1,padx=5,pady=5,sticky=tk.W)
        ttk.Label(of,text="音频:").grid(row=1,column=2,sticky=tk.W,pady=5,padx=(15,0))
        ttk.Combobox(of,textvariable=self.audio_mode_var,values=["normal","audio-only","mute"],state="readonly",width=10).grid(row=1,column=3,padx=5,pady=5,sticky=tk.W)
        ttk.Label(of,text="分辨率:").grid(row=1,column=4,sticky=tk.W,pady=5,padx=(15,0))
        ttk.Combobox(of,textvariable=self.resolution_var,values=["","3840x2160","2560x1440","1920x1080","1280x720","854x480","640x360"],width=10).grid(row=1,column=5,padx=5,pady=5,sticky=tk.W)
        ttk.Checkbutton(of,text="覆盖已存在文件",variable=self.overwrite_var).grid(row=2,column=0,columnspan=2,sticky=tk.W,pady=5)
        ttk.Checkbutton(of,text="保留字幕",variable=self.sub_var).grid(row=2,column=2,columnspan=2,sticky=tk.W,pady=5)
        ttk.Label(of,text="快速预设:").grid(row=2,column=4,sticky=tk.W,pady=5,padx=(15,0))
        self.quick_preset_cb=ttk.Combobox(of,values=["自定义"]+self.pm.get_names(),state="readonly",width=12)
        self.quick_preset_cb.grid(row=2,column=5,padx=5,pady=5,sticky=tk.W)
        self.quick_preset_cb.set("自定义")
        self.quick_preset_cb.bind("<<ComboboxSelected>>",self._on_quick_preset)
        # 视频信息
        iff=ttk.LabelFrame(p,text="视频信息",padding=8); iff.pack(fill=tk.X,pady=(0,10))
        self.info_text=tk.Text(iff,height=3,wrap=tk.WORD,font=("Consolas",9)); self.info_text.pack(fill=tk.X)
        self.info_text.insert(tk.END,"选择文件后自动显示视频信息"); self.info_text.config(state=tk.DISABLED)
        # 按钮
        bf=ttk.Frame(p); bf.pack(fill=tk.X,pady=5)
        self.convert_btn=ttk.Button(bf,text="▶ 开始转换",command=self._start_single,width=15)
        self.convert_btn.pack(side=tk.LEFT,padx=5)
        self.cancel_btn=ttk.Button(bf,text="■ 取消",command=self._cancel,state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT,padx=5)
        self.input_var.trace_add("write",lambda*_:self._on_input_change())
        self.format_var.trace_add("write",lambda*_:self._auto_gen_output())

    def _build_batch_tab(self,p):
        df=ttk.LabelFrame(p,text="目录选择",padding=10); df.pack(fill=tk.X,pady=(0,10))
        ttk.Label(df,text="输入目录:").grid(row=0,column=0,sticky=tk.W,pady=5)
        ttk.Entry(df,textvariable=self.batch_input_var,width=60).grid(row=0,column=1,padx=5,pady=5)
        ttk.Button(df,text="浏览...",command=self._choose_batch_input).grid(row=0,column=2,pady=5)
        ttk.Label(df,text="输出目录:").grid(row=1,column=0,sticky=tk.W,pady=5)
        ttk.Entry(df,textvariable=self.batch_output_var,width=60).grid(row=1,column=1,padx=5,pady=5)
        ttk.Button(df,text="浏览...",command=self._choose_batch_output).grid(row=1,column=2,pady=5)
        of=ttk.LabelFrame(p,text="批量选项",padding=10); of.pack(fill=tk.X,pady=(0,10))
        ttk.Label(of,text="格式:").grid(row=0,column=0,sticky=tk.W,pady=5)
        ttk.Combobox(of,textvariable=self.format_var,values=VideoFormat.values(),state="readonly",width=10).grid(row=0,column=1,padx=5,pady=5,sticky=tk.W)
        ttk.Label(of,text="并行数:").grid(row=0,column=2,sticky=tk.W,pady=5,padx=(15,0))
        ttk.Spinbox(of,from_=0,to=8,textvariable=self.batch_workers_var,width=6).grid(row=0,column=3,padx=5,pady=5,sticky=tk.W)
        ttk.Label(of,text="(0=自动)").grid(row=0,column=4,sticky=tk.W,pady=5)
        ttk.Label(of,text="CRF:").grid(row=0,column=5,sticky=tk.W,pady=5,padx=(15,0))
        ttk.Spinbox(of,from_=0,to=51,textvariable=self.crf_var,width=6).grid(row=0,column=6,padx=5,pady=5,sticky=tk.W)
        lf=ttk.LabelFrame(p,text="文件列表",padding=5); lf.pack(fill=tk.BOTH,expand=True,pady=(0,10))
        cols=("文件名","大小","时长","状态")
        self.file_tree=ttk.Treeview(lf,columns=cols,show="headings",height=8)
        for c in cols: self.file_tree.heading(c,text=c)
        self.file_tree.column("文件名",width=300); self.file_tree.column("大小",width=80)
        self.file_tree.column("时长",width=70); self.file_tree.column("状态",width=100)
        sb=ttk.Scrollbar(lf,orient=tk.VERTICAL,command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=sb.set)
        self.file_tree.pack(side=tk.LEFT,fill=tk.BOTH,expand=True); sb.pack(side=tk.RIGHT,fill=tk.Y)
        bf=ttk.Frame(p); bf.pack(fill=tk.X,pady=5)
        ttk.Button(bf,text="扫描文件",command=self._scan_dir).pack(side=tk.LEFT,padx=5)
        self.batch_btn=ttk.Button(bf,text="▶ 批量转换",command=self._start_batch,width=15)
        self.batch_btn.pack(side=tk.LEFT,padx=5)
        self.batch_cancel_btn=ttk.Button(bf,text="■ 取消",command=self._cancel,state=tk.DISABLED)
        self.batch_cancel_btn.pack(side=tk.LEFT,padx=5)

    def _build_compress_tab(self,p):
        """v6 新增: 压缩标签页"""
        ff=ttk.LabelFrame(p,text="文件选择",padding=10); ff.pack(fill=tk.X,pady=(0,10))
        ttk.Label(ff,text="输入:").grid(row=0,column=0,sticky=tk.W,pady=5)
        ttk.Entry(ff,textvariable=self.input_var,width=55).grid(row=0,column=1,padx=5,pady=5)
        ttk.Button(ff,text="浏览...",command=self._choose_input).grid(row=0,column=2,pady=5)
        ttk.Button(ff,text="查看信息",command=self._show_video_info).grid(row=0,column=3,padx=5,pady=5)
        ttk.Label(ff,text="输出:").grid(row=1,column=0,sticky=tk.W,pady=5)
        ttk.Entry(ff,textvariable=self.output_var,width=55).grid(row=1,column=1,padx=5,pady=5)
        ttk.Button(ff,text="浏览...",command=lambda:self._choose_output()).grid(row=1,column=2,pady=5)
        ttk.Button(ff,text="自动生成",command=lambda:self._auto_gen_output(suffix="compressed")).grid(row=1,column=3,padx=5,pady=5)
        # 压缩目标
        cf=ttk.LabelFrame(p,text="压缩目标",padding=10); cf.pack(fill=tk.X,pady=(0,10))
        ttk.Label(cf,text="压缩方法:").grid(row=0,column=0,sticky=tk.W,pady=5)
        ttk.Combobox(cf,textvariable=self.compress_method_var,values=[m.key for m in CompressMethod],state="readonly",width=12).grid(row=0,column=1,padx=5,pady=5,sticky=tk.W)
        ttk.Label(cf,text="  auto=自动选择最优方法").grid(row=0,column=2,sticky=tk.W,pady=5,padx=5)
        ttk.Label(cf,text="目标大小(MB):").grid(row=1,column=0,sticky=tk.W,pady=5)
        ttk.Entry(cf,textvariable=self.compress_target_mb_var,width=12).grid(row=1,column=1,padx=5,pady=5,sticky=tk.W)
        ttk.Label(cf,text="目标比例:").grid(row=1,column=2,sticky=tk.W,pady=5,padx=(15,0))
        ttk.Combobox(cf,textvariable=self.compress_ratio_var,values=["","0.9","0.7","0.5","0.3","0.2","0.1"],width=8).grid(row=1,column=3,padx=5,pady=5,sticky=tk.W)
        ttk.Label(cf,text="(0.5=压缩到50%)").grid(row=1,column=4,sticky=tk.W,pady=5,padx=5)
        ttk.Label(cf,text="音频(kbps):").grid(row=2,column=0,sticky=tk.W,pady=5)
        ttk.Spinbox(cf,from_=32,to=512,textvariable=self.compress_audio_br_var,width=8).grid(row=2,column=1,padx=5,pady=5,sticky=tk.W)
        ttk.Label(cf,text="编码预设:").grid(row=2,column=2,sticky=tk.W,pady=5,padx=(15,0))
        ttk.Combobox(cf,textvariable=self.preset_var,values=EncodingPreset.values(),state="readonly",width=10).grid(row=2,column=3,padx=5,pady=5,sticky=tk.W)
        # 压缩预设
        ttk.Label(cf,text="快速压缩预设:").grid(row=3,column=0,sticky=tk.W,pady=5)
        self.cp_combo=ttk.Combobox(cf,values=["自定义"]+self.pm.get_compress_names(),state="readonly",width=20)
        self.cp_combo.grid(row=3,column=1,columnspan=2,padx=5,pady=5,sticky=tk.W)
        self.cp_combo.set("自定义")
        self.cp_combo.bind("<<ComboboxSelected>>",self._on_compress_preset)
        ttk.Button(cf,text="估算压缩后大小",command=self._estimate_size).grid(row=3,column=3,columnspan=2,padx=5,pady=5,sticky=tk.W)
        # 预估结果
        ef=ttk.LabelFrame(p,text="预估结果",padding=8); ef.pack(fill=tk.X,pady=(0,10))
        self.est_text=tk.Text(ef,height=2,wrap=tk.WORD,font=("Consolas",9)); self.est_text.pack(fill=tk.X)
        self.est_text.insert(tk.END,"选择视频文件后点击「估算压缩后大小」"); self.est_text.config(state=tk.DISABLED)
        # 按钮
        bf=ttk.Frame(p); bf.pack(fill=tk.X,pady=5)
        self.compress_btn=ttk.Button(bf,text="▶ 开始压缩",command=self._start_compress,width=15)
        self.compress_btn.pack(side=tk.LEFT,padx=5)
        self.compress_cancel_btn=ttk.Button(bf,text="■ 取消",command=self._cancel,state=tk.DISABLED)
        self.compress_cancel_btn.pack(side=tk.LEFT,padx=5)

    def _build_preset_tab(self,p):
        lf=ttk.LabelFrame(p,text="可用预设",padding=5); lf.pack(fill=tk.BOTH,expand=True,pady=(0,10))
        cols=("名称","视频","音频","CRF","预设","硬件","描述")
        self.preset_tree=ttk.Treeview(lf,columns=cols,show="headings",height=10)
        for i,c in enumerate(cols):
            self.preset_tree.heading(c,text=c)
            self.preset_tree.column(c,width=60 if i in(3,4,5) else 120)
        sb=ttk.Scrollbar(lf,orient=tk.VERTICAL,command=self.preset_tree.yview)
        self.preset_tree.configure(yscrollcommand=sb.set)
        self.preset_tree.pack(side=tk.LEFT,fill=tk.BOTH,expand=True); sb.pack(side=tk.RIGHT,fill=tk.Y)
        self._refresh_preset_tree()
        bf=ttk.Frame(p); bf.pack(fill=tk.X,pady=5)
        ttk.Button(bf,text="应用预设",command=self._apply_preset).pack(side=tk.LEFT,padx=5)
        ttk.Button(bf,text="保存当前为预设",command=self._save_as_preset).pack(side=tk.LEFT,padx=5)
        ttk.Button(bf,text="删除预设",command=self._delete_preset).pack(side=tk.LEFT,padx=5)

    # ---- Actions ----
    def _log(self,msg:str): self.log_queue.put(msg)
    def _poll_logs(self):
        while True:
            try: line=self.log_queue.get_nowait()
            except queue.Empty: break
            self.log_text.insert(tk.END,line+"\n"); self.log_text.see(tk.END)
        self.root.after(120,self._poll_logs)
    def _choose_input(self):
        p=filedialog.askopenfilename(title="选择视频文件",
            filetypes=[("视频文件","*.mp4 *.mkv *.webm *.mov *.avi *.flv *.wmv *.mpeg"),("所有文件","*.*")])
        if p: self.input_var.set(p); self._auto_gen_output()
    def _choose_output(self):
        ext=self.format_var.get()
        p=filedialog.asksaveasfilename(title="保存为",defaultextension=f".{ext}",
            filetypes=[(f"{ext.upper()}文件",f"*.{ext}"),("所有文件","*.*")])
        if p: self.output_var.set(p)
    def _auto_gen_output(self,suffix:str=""):
        inp=self.input_var.get().strip(); fmt=self.format_var.get()
        if inp and fmt: self.output_var.set(build_output_path(inp,fmt,suffix))
    def _on_input_change(self):
        p=self.input_var.get().strip()
        if p and Path(p).exists(): self._show_video_info()
    def _show_video_info(self):
        p=self.input_var.get().strip()
        if not p: return
        info=get_video_info(p)
        self.info_text.config(state=tk.NORMAL); self.info_text.delete(1.0,tk.END)
        if info:
            lines=[f"文件:{Path(p).name}  大小:{info.size_str} ({info.size_mb:.1f}MB)",
                   f"分辨率:{info.resolution}  时长:{info.duration_str}  帧率:{info.fps:.2f}fps",
                   f"视频编码:{info.video_codec or '未知'}({info.video_bitrate/1000:.0f}kbps)  音频:{info.audio_codec or '未知'}({info.audio_bitrate/1000:.0f}kbps)",
                   f"字幕:{'有' if info.has_subtitles else '无'}"]
            self.info_text.insert(tk.END,"\n".join(lines))
            self.quick_preset_cb['values']=["自定义"]+self.pm.get_names()
            self.cp_combo['values']=["自定义"]+self.pm.get_compress_names()
        else: self.info_text.insert(tk.END,"无法获取视频信息")
        self.info_text.config(state=tk.DISABLED)
    def _on_quick_preset(self,event):
        name=self.quick_preset_cb.get()
        if name=="自定义": return
        pr=self.pm.get(name)
        if pr:
            self.crf_var.set(pr.crf); self.preset_var.set(pr.preset.value)
            self.hw_var.set(pr.hw_accel.key); self._log(f"应用预设:{pr.name}")
    def _on_compress_preset(self,event):
        name=self.cp_combo.get()
        if name=="自定义": return
        cp=self.pm.get_compress(name)
        if cp:
            self.compress_method_var.set(cp.method.key)
            if cp.target_size_mb: self.compress_target_mb_var.set(str(cp.target_size_mb))
            else: self.compress_target_mb_var.set("")
            if cp.target_ratio: self.compress_ratio_var.set(str(cp.target_ratio))
            else: self.compress_ratio_var.set("")
            if cp.crf: self.crf_var.set(cp.crf)
            if cp.resolution: self.resolution_var.set(cp.resolution)
            self._log(f"应用压缩预设:{cp.name}")
    def _estimate_size(self):
        p=self.input_var.get().strip()
        if not p: messagebox.showwarning("提示","请先选择输入视频"); return
        info=get_video_info(p)
        if not info: messagebox.showerror("错误","无法获取视频信息"); return
        crf=self.crf_var.get(); res=self.resolution_var.get().strip() or None
        est=estimate_compressed_size(info,crf,res)
        self.est_text.config(state=tk.NORMAL); self.est_text.delete(1.0,tk.END)
        lines=[f"预估输出大小: {est['estimated_mb']:.1f}MB ({est['estimated_ratio']*100:.0f}% 原始)",
               f"推荐比特率: {est['recommended_bitrate']/1000:.0f}kbps  置信度: {est['confidence']}"]
        self.est_text.insert(tk.END,"\n".join(lines)); self.est_text.config(state=tk.DISABLED)
        if info.size_mb>0 and est["estimated_ratio"]>0:
            tmb=info.size_mb*est["estimated_ratio"]
            self.compress_target_mb_var.set(f"{tmb:.1f}")
            self.compress_ratio_var.set(f"{est['estimated_ratio']:.2f}")
        self._log(f"预估:CRF={crf} -> 约{est['estimated_mb']:.1f}MB")
    def _get_hw(self)->HwAccel:
        for a in HwAccel:
            if a.key==self.hw_var.get(): return a
        return HwAccel.NONE
    def _cancel(self):
        self.cancel_event.set(); self._log("正在取消...")
        self.convert_btn.config(state=tk.DISABLED); self.cancel_btn.config(state=tk.DISABLED)
        self.batch_btn.config(state=tk.DISABLED); self.batch_cancel_btn.config(state=tk.DISABLED)
        self.compress_btn.config(state=tk.DISABLED); self.compress_cancel_btn.config(state=tk.DISABLED)
    def _choose_batch_input(self):
        p=filedialog.askdirectory(title="选择输入目录")
        if p: self.batch_input_var.set(p)
    def _choose_batch_output(self):
        p=filedialog.askdirectory(title="选择输出目录")
        if p: self.batch_output_var.set(p)
    def _scan_dir(self):
        d=self.batch_input_var.get().strip()
        if not d: messagebox.showwarning("提示","请选择输入目录"); return
        dp=Path(d)
        if not dp.exists(): messagebox.showerror("错误","输入目录不存在"); return
        for item in self.file_tree.get_children(): self.file_tree.delete(item)
        exts=[f".{f}" for f in VideoFormat.values()]
        files=[]
        for ext in exts:
            files.extend(dp.glob(f"*{ext}")); files.extend(dp.glob(f"*{ext.upper()}"))
        for f in sorted(set(files)):
            info=get_video_info(f)
            self.file_tree.insert("",tk.END,values=(f.name,info.size_str if info else "?",info.duration_str if info else "?","等待"),tags=(str(f),))
        self._log(f"扫描完成:{len(files)}个文件")
    def _refresh_preset_tree(self):
        for item in self.preset_tree.get_children(): self.preset_tree.delete(item)
        for p in self.pm.presets.values():
            self.preset_tree.insert("",tk.END,values=(p.name,p.video_codec.description,p.audio_codec.description,p.crf,p.preset.value,p.hw_accel.label,p.description))
    def _apply_preset(self):
        sel=self.preset_tree.selection()
        if not sel: return
        name=self.preset_tree.item(sel[0])["values"][0]
        pr=self.pm.get(name)
        if pr:
            self.crf_var.set(pr.crf); self.preset_var.set(pr.preset.value)
            self.hw_var.set(pr.hw_accel.key); self._log(f"应用预设:{pr.name}")
    def _save_as_preset(self):
        name=f"自定义_{int(time.time())%10000}"
        hw=self._get_hw()
        pr=ConversionPreset(name=name,video_codec=VideoCodec.H264,audio_codec=AudioCodec.AAC,
                            crf=self.crf_var.get(),preset=EncodingPreset(self.preset_var.get()),
                            hw_accel=hw,description="用户自定义")
        self.pm.add(pr); self._refresh_preset_tree()
        self.quick_preset_cb['values']=["自定义"]+self.pm.get_names(); self._log(f"保存预设:{name}")
    def _delete_preset(self):
        sel=self.preset_tree.selection()
        if not sel: return
        name=self.preset_tree.item(sel[0])["values"][0]
        if self.pm.delete(name):
            self._refresh_preset_tree()
            self.quick_preset_cb['values']=["自定义"]+self.pm.get_names(); self._log(f"删除预设:{name}")
        else: messagebox.showwarning("提示","不能删除默认预设")
    def _set_busy(self,busy:bool):
        self.convert_btn.config(state=tk.DISABLED if busy else tk.NORMAL)
        self.cancel_btn.config(state=tk.NORMAL if busy else tk.DISABLED)
        self.batch_btn.config(state=tk.DISABLED if busy else tk.NORMAL)
        self.batch_cancel_btn.config(state=tk.NORMAL if busy else tk.DISABLED)
        self.compress_btn.config(state=tk.DISABLED if busy else tk.NORMAL)
        self.compress_cancel_btn.config(state=tk.NORMAL if busy else tk.DISABLED)
    def _start_single(self):
        inp=self.input_var.get().strip(); out=self.output_var.get().strip()
        if not inp or not out: messagebox.showerror("错误","请选择输入和输出文件"); return
        self.cancel_event.clear(); self.converting=True; self._set_busy(True)
        self.progress_var.set(0); self.status_var.set("转换中...")
        hw=self._get_hw(); res=self.resolution_var.get().strip() or None
        def worker():
            ec,err=convert_video(input_path=inp,output_path=out,overwrite=self.overwrite_var.get(),
                crf=self.crf_var.get(),preset=self.preset_var.get(),hw_accel=hw,
                audio_mode=self.audio_mode_var.get(),resolution=res,keep_subtitles=self.sub_var.get(),
                log_callback=lambda m:self.log_queue.put(m),
                progress_callback=lambda p,s:self.root.after(0,lambda:self._update_progress(p,s)),
                cancel_event=self.cancel_event)
            self.root.after(0,lambda:self._conversion_done(ec,err))
        threading.Thread(target=worker,daemon=True).start()
    def _update_progress(self,progress:float,detail:str):
        self.progress_var.set(progress*100); self.status_var.set(f"转换中 [{detail}]")
    def _conversion_done(self,code:int,error:Optional[str]):
        self.converting=False; self._set_busy(False)
        if code==0:
            self.progress_var.set(100); self.status_var.set("完成")
            messagebox.showinfo("成功","转换完成！")
        else:
            self.status_var.set(f"失败:{error}"); messagebox.showerror("失败",error or "未知错误")
    def _start_batch(self):
        inp=self.batch_input_var.get().strip(); out=self.batch_output_var.get().strip()
        if not inp or not out: messagebox.showwarning("提示","请选择输入和输出目录"); return
        items=self.file_tree.get_children()
        if not items: messagebox.showwarning("提示","无文件，请先扫描"); return
        files=[Path(inp)/self.file_tree.item(it)["values"][0] for it in items]
        for it in items:
            v=self.file_tree.item(it)["values"]; self.file_tree.item(it,values=(v[0],v[1],v[2],"等待"))
        self.cancel_event.clear(); self.converting=True; self._set_busy(True)
        self.status_var.set("批量转换中..."); self.progress_var.set(0)
        workers=self.batch_workers_var.get() if self.batch_workers_var.get()>0 else None
        hw=self._get_hw(); res=self.resolution_var.get().strip() or None
        def pc(prog:ConversionProgress):
            self.root.after(0,lambda:self._update_batch_progress(prog))
        def worker():
            results=convert_batch_parallel(input_files=files,output_dir=out,target_format=self.format_var.get(),
                overwrite=self.overwrite_var.get(),crf=self.crf_var.get(),preset=self.preset_var.get(),
                max_workers=workers,hw_accel=hw,audio_mode=self.audio_mode_var.get(),
                resolution=res,keep_subtitles=self.sub_var.get(),
                log_callback=lambda m:self.log_queue.put(m),progress_callback=pc,cancel_event=self.cancel_event)
            self.root.after(0,lambda:self._batch_done(results))
        threading.Thread(target=worker,daemon=True).start()
    def _update_batch_progress(self,prog:ConversionProgress):
        self.progress_var.set(prog.get_overall_progress()*100); self.status_var.set(prog.format_progress())
        if prog.current_file_name:
            for item in self.file_tree.get_children():
                v=self.file_tree.item(item)["values"]
                if v[0]==prog.current_file_name:
                    self.file_tree.item(item,values=(v[0],v[1],v[2],f"{prog.current_file_progress*100:.0f}%")); break
    def _batch_done(self,results:Dict):
        self.converting=False; self._set_busy(False)
        self.status_var.set(f"完成:{results['success']}成功/{results['failed']}失败")
        msg=f"批量转换完成!\n成功:{results['success']}\n失败:{results['failed']}"
        if results["failed"]>0: msg+="\n\n失败文件:\n"+"\n".join(f["file"] for f in results["failed_files"][:10])
        messagebox.showinfo("批量转换结果",msg)
    # ---- v6 压缩操作 ----
    def _start_compress(self):
        inp=self.input_var.get().strip(); out=self.output_var.get().strip()
        if not inp or not out: messagebox.showerror("错误","请选择输入和输出文件"); return
        self.cancel_event.clear(); self.converting=True; self._set_busy(True)
        self.progress_var.set(0); self.status_var.set("压缩中...")
        # 解析参数
        method=CompressMethod.AUTO
        for m in CompressMethod:
            if m.key==self.compress_method_var.get(): method=m; break
        tmb_s=self.compress_target_mb_var.get().strip()
        tr_s=self.compress_ratio_var.get().strip()
        tmb=float(tmb_s) if tmb_s else None
        tr=float(tr_s) if tr_s else None
        hw=self._get_hw(); res=self.resolution_var.get().strip() or None
        abr=self.compress_audio_br_var.get()*1000
        def worker():
            ec,err=compress_video(input_path=inp,output_path=out,target_size_mb=tmb,target_ratio=tr,
                method=method,crf=self.crf_var.get(),preset=self.preset_var.get(),
                resolution=res,hw_accel=hw,audio_bitrate=abr,overwrite=self.overwrite_var.get(),
                log_callback=lambda m:self.log_queue.put(m),
                progress_callback=lambda p,s,cp,tp:self.root.after(0,lambda:self._update_compress_progress(p,s,cp,tp)),
                cancel_event=self.cancel_event)
            self.root.after(0,lambda:self._compress_done(ec,err))
        threading.Thread(target=worker,daemon=True).start()
    def _update_compress_progress(self,progress:float,detail:str,cur_pass:int,total_passes:int):
        pass_str=f" [第{cur_pass}/{total_passes}遍]" if total_passes>1 else ""
        self.progress_var.set(progress*100); self.status_var.set(f"压缩中{pass_str} [{detail}]")
    def _compress_done(self,code:int,error:Optional[str]):
        self.converting=False; self._set_busy(False)
        if code==0:
            self.progress_var.set(100); self.status_var.set("压缩完成")
            # 显示实际输出大小
            out=self.output_var.get().strip()
            if out and Path(out).exists():
                asz=Path(out).stat().st_size/(1024*1024)
                msg=f"压缩完成!\n实际输出大小:{asz:.1f}MB"
            else: msg="压缩完成!"
            messagebox.showinfo("成功",msg)
        else:
            self.status_var.set(f"压缩失败:{error}"); messagebox.showerror("失败",error or "未知错误")

# ===========================================================================
# CLI
# ===========================================================================

def build_cli()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="视频格式转换器 v6 (含压缩功能)")
    sub=p.add_subparsers(dest="command",help="子命令")
    # 转换
    cvt=sub.add_parser("convert",help="转换视频格式")
    cvt.add_argument("input",help="输入文件路径")
    cvt.add_argument("-o","--output",help="输出文件路径")
    cvt.add_argument("-f","--format",choices=VideoFormat.values(),help="目标格式")
    cvt.add_argument("--crf",type=int,default=23,help="CRF值(0-51,默认23)")
    cvt.add_argument("--preset",default="medium",choices=EncodingPreset.values(),help="编码预设")
    cvt.add_argument("--overwrite",action="store_true",help="覆盖输出文件")
    cvt.add_argument("--hw",default="none",choices=[a.key for a in HwAccel],help="硬件加速")
    cvt.add_argument("--resolution",help="缩放分辨率(如1920x1080)")
    cvt.add_argument("--audio-mode",default="normal",choices=["normal","audio-only","mute"],help="音频模式")
    cvt.add_argument("--no-subs",action="store_true",help="不保留字幕")
    # 压缩 (v6 新增)
    cmp=sub.add_parser("compress",help="压缩视频文件大小")
    cmp.add_argument("input",help="输入文件路径")
    cmp.add_argument("-o","--output",help="输出文件路径")
    cmp.add_argument("--target-mb",type=float,help="目标文件大小(MB)")
    cmp.add_argument("--target-ratio",type=float,help="目标比例(如0.5=压缩到50%%)")
    cmp.add_argument("--method",default="auto",choices=[m.key for m in CompressMethod],help="压缩方法")
    cmp.add_argument("--crf",type=int,default=28,help="CRF值(CRF模式,默认28)")
    cmp.add_argument("--preset",default="medium",choices=EncodingPreset.values(),help="编码预设")
    cmp.add_argument("--audio-bitrate",type=int,default=128,help="音频比特率kbps")
    cmp.add_argument("--resolution",help="缩放分辨率")
    cmp.add_argument("--overwrite",action="store_true",help="覆盖输出文件")
    # 批量
    bat=sub.add_parser("batch",help="批量转换")
    bat.add_argument("input_dir",help="输入目录")
    bat.add_argument("output_dir",help="输出目录")
    bat.add_argument("-f","--format",required=True,choices=VideoFormat.values(),help="目标格式")
    bat.add_argument("--crf",type=int,default=23,help="CRF值")
    bat.add_argument("--preset",default="medium",choices=EncodingPreset.values(),help="编码预设")
    bat.add_argument("--workers",type=int,default=0,help="并行数(0=自动)")
    bat.add_argument("--overwrite",action="store_true",help="覆盖输出文件")
    # 信息
    info=sub.add_parser("info",help="查看视频信息")
    info.add_argument("input",help="视频文件路径")
    # 预估 (v6 新增)
    est=sub.add_parser("estimate",help="预估压缩后大小")
    est.add_argument("input",help="视频文件路径")
    est.add_argument("--crf",type=int,default=28,help="CRF值")
    est.add_argument("--resolution",help="缩放分辨率")
    return p

def run_cli()->int:
    parser=build_cli(); args=parser.parse_args()
    if not has_ffmpeg(): print("错误:ffmpeg未安装"); return 1
    if args.command=="convert":
        inp=args.input
        if not args.output:
            if not args.format: print("错误:需要--output或--format"); return 1
            outp=build_output_path(inp,args.format)
        else: outp=args.output
        hw=HwAccel.NONE
        for a in HwAccel:
            if a.key==args.hw: hw=a; break
        ec,err=convert_video(input_path=inp,output_path=outp,overwrite=args.overwrite,
            crf=args.crf,preset=args.preset,hw_accel=hw,
            audio_mode=args.audio_mode,resolution=args.resolution,
            keep_subtitles=not args.no_subs,log_callback=print)
        if ec==0: print(f"完成:{outp}")
        else: print(f"失败:{err}"); return ec
    elif args.command=="compress":
        inp=args.input
        if not args.output:
            outp=build_output_path(inp,"mp4","compressed")
        else: outp=args.output
        method=CompressMethod.AUTO
        for m in CompressMethod:
            if m.key==args.method: method=m; break
        ec,err=compress_video(input_path=inp,output_path=outp,
            target_size_mb=args.target_mb,target_ratio=args.target_ratio,
            method=method,crf=args.crf,preset=args.preset,
            resolution=args.resolution,audio_bitrate=args.audio_bitrate*1000,
            overwrite=args.overwrite,log_callback=print)
        if ec==0: print(f"压缩完成:{outp}")
        else: print(f"压缩失败:{err}"); return ec
    elif args.command=="batch":
        files=[]
        exts=[f".{f}" for f in VideoFormat.values()]
        dp=Path(args.input_dir)
        for ext in exts:
            files.extend(dp.glob(f"*{ext}")); files.extend(dp.glob(f"*{ext.upper()}"))
        files=sorted(set(files))
        if not files: print("未找到视频文件"); return 1
        print(f"找到{len(files)}个文件")
        results=convert_batch_parallel(input_files=files,output_dir=args.output_dir,
            target_format=args.format,overwrite=args.overwrite,
            crf=args.crf,preset=args.preset,
            max_workers=args.workers if args.workers>0 else None,log_callback=print)
        print(f"完成:成功{results['success']}/失败{results['failed']}")
        if results["failed"]>0:
            for f in results["failed_files"]: print(f"  失败:{f['file']}:{f['error']}")
    elif args.command=="info":
        info=get_video_info(args.input)
        if info:
            print(f"文件:{info.path}")
            print(f"大小:{info.size_str}({info.size_mb:.1f}MB)")
            print(f"分辨率:{info.resolution}  时长:{info.duration_str}  帧率:{info.fps:.2f}fps")
            print(f"视频编码:{info.video_codec}({info.video_bitrate/1000:.0f}kbps)")
            print(f"音频编码:{info.audio_codec}({info.audio_bitrate/1000:.0f}kbps)")
            print(f"字幕:{'有' if info.has_subtitles else '无'}")
        else: print("无法获取视频信息"); return 1
    elif args.command=="estimate":
        info=get_video_info(args.input)
        if not info: print("无法获取视频信息"); return 1
        print(f"原始大小:{info.size_str}({info.size_mb:.1f}MB)")
        est=estimate_compressed_size(info,args.crf,args.resolution)
        print(f"预估压缩后:{est['estimated_mb']:.1f}MB({est['estimated_ratio']*100:.0f}%)")
        print(f"推荐比特率:{est['recommended_bitrate']/1000:.0f}kbps")
        print(f"置信度:{est['confidence']}")
    else:
        # 无子命令:兼容v1 CLI
        parser2=argparse.ArgumentParser(description="视频格式转换(兼容v1)")
        parser2.add_argument("input",nargs="?",help="输入文件")
        parser2.add_argument("-o","--output",help="输出文件")
        parser2.add_argument("-f","--format",help="目标格式")
        parser2.add_argument("--crf",type=int,default=23)
        parser2.add_argument("--preset",default="medium")
        parser2.add_argument("--overwrite",action="store_true")
        parser2.add_argument("--compress",action="store_true",help="压缩模式(v6)")
        parser2.add_argument("--target-mb",type=float,help="目标大小MB")
        parser2.add_argument("--target-ratio",type=float,help="目标比例")
        try: args2=parser2.parse_args()
        except SystemExit: return 0
        if not args2.input: parser2.print_help(); return 0
        inp=args2.input
        if not args2.output:
            if not args2.format: print("需要--output或--format"); return 1
            outp=build_output_path(inp,args2.format)
        else: outp=args2.output
        if args2.compress:
            ec,err=compress_video(input_path=inp,output_path=outp,
                target_size_mb=args2.target_mb,target_ratio=args2.target_ratio,
                overwrite=args2.overwrite,log_callback=print)
        else:
            ec,err=convert_video(input_path=inp,output_path=outp,
                overwrite=args2.overwrite,crf=args2.crf,preset=args2.preset,
                log_callback=print)
        if ec==0: print(f"完成:{outp}")
        else: print(f"失败:{err}")
    return 0

def run_gui()->None:
    root=tk.Tk()
    ConverterGUI(root)
    root.mainloop()

if __name__=="__main__":
    if len(sys.argv)>1: sys.exit(run_cli())
    else: run_gui()

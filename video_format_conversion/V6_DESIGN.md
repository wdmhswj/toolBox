# v6 视频压缩功能设计方案

## 新增功能概述

在 v5 全部功能基础上，新增**视频文件大小压缩**功能，支持将视频文件大小压缩到指定目标。

---

## 核心设计

### 1. 压缩策略

| 策略 | 精度 | 速度 | 适用场景 |
|------|------|------|----------|
| **两遍编码 (Two-Pass)** | ⭐⭐⭐ 精确 | 慢（2倍时间） | 需要精确控制文件大小 |
| **CRF 调整** | ⭐ 粗略 | 快（1倍时间） | 大致压缩即可 |
| **分辨率缩放 + CRF** | ⭐⭐ 较准 | 快 | 配合分辨率缩小 |

v6 主推两遍编码，同时支持 CRF 快速模式。

### 2. 两遍编码原理

```
目标视频比特率 = (目标文件大小 - 音频数据大小) / 视频时长 × 8
```

- **第一遍**：分析视频复杂度，生成日志文件
- **第二遍**：根据分析结果，按目标比特率编码

### 3. 新增函数

```python
def compress_video(
    input_path, output_path,
    target_size_mb=None,        # 目标大小(MB)
    target_ratio=None,           # 目标比例(0.0-1.0)，如0.5=压缩到50%
    method="two-pass",          # two-pass / crf / auto
    ...
) -> Tuple[int, Optional[str]]

def estimate_compressed_size(
    input_path, crf, resolution=None
) -> Optional[float]            # 返回预估大小(MB)

def calculate_target_bitrate(
    duration_sec, target_size_bytes, audio_bitrate
) -> int                        # 返回视频比特率(bps)
```

### 4. 新增 GUI 组件
- 压缩标签页（目标大小/比例输入）
- 预估大小显示
- 压缩方法选择（两遍/CRF/自动）
- 压缩进度（两遍编码有2个阶段）

### 5. 压缩预设
- 压缩到 50%（平衡质量与大小）
- 压缩到 30%（大幅压缩）
- 压缩到 100MB
- 压缩到 50MB
- 自定义

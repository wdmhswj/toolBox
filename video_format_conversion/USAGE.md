# 视频格式转换工具说明

本文档说明 `main.py` 中已实现的视频格式转换功能，包括命令行模式与 GUI 图形界面模式。

## 1. 功能概览

该工具支持常见视频格式之间的转换，例如：

- `webm -> mp4`
- `mkv -> mp4`
- `mp4 -> webm`

支持格式（当前版本）：

- `mp4`
- `mkv`
- `webm`
- `mov`
- `avi`
- `flv`

工具包含两种运行模式：

- 命令行模式（适合脚本化和批处理）
- GUI 图形界面模式（适合可视化操作）

## 2. 运行前准备

本工具依赖 `ffmpeg`。

请先确认：

1. 系统已安装 `ffmpeg`
2. `ffmpeg` 已加入系统 `PATH`

可在终端测试：

```powershell
ffmpeg -version
```

如果提示命令不存在，请先安装并配置环境变量。

## 3. 启动方式

在 `main.py` 所在目录执行：

```powershell
python main.py
```

默认进入 GUI 模式。

也可显式指定：

```powershell
python main.py gui
```

命令行转换模式：

```powershell
python main.py convert -i input.webm -o output.mp4 --overwrite
```

## 4. 命令行参数说明

命令：

```powershell
python main.py convert [参数]
```

参数说明：

- `-i, --input`：输入视频路径（必填）
- `-o, --output`：输出视频完整路径（可选）
- `-f, --format`：目标格式（可选；当未提供 `--output` 时可用它自动生成输出文件名）
- `--overwrite`：若输出文件已存在则覆盖
- `--crf`：视频质量参数，范围 `0-51`，数值越小质量越高、体积通常越大（默认 `23`）
- `--preset`：编码速度/压缩效率预设（默认 `medium`）

可选 `preset`：

- `ultrafast`
- `superfast`
- `veryfast`
- `faster`
- `fast`
- `medium`
- `slow`
- `slower`
- `veryslow`

### 命令行示例

1) 指定输入输出：

```powershell
python main.py convert -i demo.webm -o demo.mp4 --overwrite
```

2) 只指定目标格式，自动生成输出路径：

```powershell
python main.py convert -i demo.webm -f mp4 --overwrite
```

3) 调整质量与速度：

```powershell
python main.py convert -i demo.mkv -o demo.mp4 --crf 20 --preset slow --overwrite
```

## 5. GUI 界面说明

运行 `python main.py` 或 `python main.py gui` 后会打开图形界面。

### 5.1 File 区域

- `Input`：输入视频文件路径
  - `Browse` 按钮可打开文件选择框
- `Output`：输出视频文件路径
  - `Browse` 按钮可手动选择保存位置

### 5.2 Options 区域

- `Target format`：目标格式下拉选择（如 `mp4`）
- `Build output path`：根据输入文件名和目标格式自动生成输出路径
- `CRF`：视频质量参数（`0-51`）
  - 小：画质更好、体积更大
  - 大：体积更小、画质更低
- `Preset`：编码速度与压缩效率平衡
  - 快速预设（如 `ultrafast`）：编码快，压缩率通常较差
  - 慢速预设（如 `slow`/`veryslow`）：编码慢，压缩率通常更好
- `Overwrite output`：勾选后，若输出文件存在则覆盖

### 5.3 操作与日志

- `Start Conversion`：开始转换（后台线程执行，界面不会卡住）
- `Status`：显示当前状态（如 `Ready`、`Converting...`、`Completed`）
- `Logs`：实时显示 `ffmpeg` 输出日志，便于排查问题

## 6. 编码策略说明（当前实现）

程序会根据输出扩展名自动选择常见编码器：

- `mp4/mov/mkv`：`libx264 + aac`
- `webm`：`libvpx-vp9 + libopus`
- `avi`：`mpeg4 + mp3`
- `flv`：`flv + aac`

## 7. 常见问题

### 7.1 提示 ffmpeg 不存在

原因：系统找不到 `ffmpeg` 命令。

解决：安装 ffmpeg 并将其可执行目录加入 PATH 后重启终端。

### 7.2 输出文件已存在，转换失败

原因：未开启覆盖模式。

解决：

- 命令行增加 `--overwrite`
- GUI 中勾选 `Overwrite output`

### 7.3 转换速度较慢

可尝试：

- 将 `preset` 调整为 `fast` 或 `veryfast`
- 适度提高 `crf`（如 `23 -> 26`）

## 8. 建议

- 若是批量任务，优先使用命令行模式。
- 若是偶发转换和人工选择文件，使用 GUI 更方便。
- 转换前先用短视频测试参数组合，再处理正式素材。

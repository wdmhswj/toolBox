# toolBox 项目分析文档

> 生成日期: 2026-03-16
> 生成者: Claude Code

## 项目概述

**toolBox** 是一个多功能工具集合项目，包含多个独立的功能模块。项目使用中文文档和代码注释，解决文件处理、媒体转换、安全生成等实际问题。

## 模块功能总览

### 1. 文件比较工具 (`file_comparison/`)
- **基础版** (`file_compare.html`): 文件差异对比器，支持文件上传/文本输入
- **高级版** (`file_compare_v2.html` - NexDiff):
  - 暗色/亮色主题切换
  - 字符级差异高亮
  - 导出功能
  - 现代化UI设计
- **技术**: 纯HTML/CSS/JavaScript，无需后端

### 2. 视频格式转换 (`video_format_conversion/`)
- **功能**: mp4/mkv/webm/mov/avi/flv 格式互转
- **模式**: 命令行 + GUI界面
- **依赖**: ffmpeg (必须安装并加入PATH)
- **文档**: `USAGE.md` 包含完整使用说明
- **编码器自动选择**:
  - mp4/mov/mkv → libx264 + aac
  - webm → libvpx-vp9 + libopus
  - avi → mpeg4 + mp3
  - flv → flv + aac

### 3. 密码生成器 (`src/PassswordGenenrator/`)
- **注意**: 目录名有拼写错误 ("PassswordGenenrator")
- **功能**: 生成指定长度的随机密码
- **字符集**: 大写字母、小写字母、数字、特殊字符 (!@#$%^&*()_+)
- **实现**: C++类 `PasswordGenerator`
- **安全问题**: 使用 `std::rand()` 不是密码学安全的

### 4. 文件哈希重命名 (`src/hash_wangwen/`)
- **脚本演进**:
  1. `script.ps1` - 基础版本，处理.txt文件
  2. `script2.ps1` - 支持多扩展名，添加统计
  3. `script3.ps1` - 完整版，含日志记录和文件映射报告
- **功能**: 计算文件哈希值，根据哈希重命名文件
- **用途**: 文件去重、归档标准化命名

### 5. 学号处理工具 (`src/name_xuehao/`)
- **功能**: 读取Excel文件，提取和处理学号数据
- **技术**: Python + pandas库
- **包含**: HTTP请求示例（访问成绩查询API）

## 技术栈
- **前端**: HTML/CSS/JavaScript
- **脚本语言**: Python, PowerShell, C++
- **关键依赖**: ffmpeg, pandas
- **平台**: 主要面向Windows (PowerShell脚本)

## 项目结构
```
toolBox/
├── README.md                    # 项目总说明
├── file_comparison/             # 文件比较工具
│   ├── file_compare.html        # 基础版
│   └── file_compare_v2.html     # 高级版 NexDiff
├── video_format_conversion/     # 视频转换
│   ├── main.py                  # 主程序
│   └── USAGE.md                 # 详细文档
└── src/                         # 源代码
    ├── PassswordGenenrator/     # C++密码生成器
    ├── hash_wangwen/           # PowerShell哈希重命名
    └── name_xuehao/            # Python学号处理
```

## 开发历史（最近提交）
- `f5e218d` 添加视频格式转换模块
- `c33278c` add file_compare module
- `dbad266` add the hash_wangwen module

## 优点与亮点
1. **模块化设计**: 各功能独立，便于维护和扩展
2. **文档完整**: 视频转换模块有详细的USAGE.md
3. **用户体验**: 文件比较工具界面美观，提供两种版本
4. **实用性强**: 解决实际开发和工作中的常见需求

## 待改进项

### 高优先级
1. **密码生成器安全**: 使用 `<random>` 替代 `std::rand()`
2. **目录命名**: 修复 "PassswordGenenrator" 拼写错误
3. **依赖声明**: 为Python模块添加 requirements.txt

### 中优先级
1. **统一入口点**: 创建 `toolbox --help` 命令行接口
2. **错误处理**: 统一各模块的错误处理规范
3. **输入验证**: 添加必要的输入验证和边界检查

### 低优先级
1. **国际化**: 部分中文注释可考虑添加英文版本
2. **测试覆盖**: 添加单元测试
3. **CI/CD**: 设置自动化构建和测试

## 扩展建议

### 功能扩展
1. **文件比较工具**: 添加大文件支持，后端处理
2. **视频转换**: 添加批量处理、预设模板
3. **密码生成器**: 添加密码强度评估、历史记录

### 架构改进
1. **统一配置**: 创建共享配置管理系统
2. **插件化**: 支持第三方工具集成
3. **Web界面**: 创建统一的Web控制台

## 使用场景
- **开发者**: 代码差异比较
- **内容创作者**: 视频格式转换
- **系统管理员**: 文件批量处理
- **教育工作者**: 学生信息处理

## 快速开始

### 视频格式转换
```bash
cd video_format_conversion
python main.py gui
# 或命令行模式
python main.py convert -i input.webm -o output.mp4 --overwrite
```

### 文件比较
直接在浏览器中打开 `file_comparison/file_compare_v2.html`

### 密码生成
```bash
cd src/PassswordGenenrator
# 需要编译C++代码
```

### 文件哈希重命名
```powershell
cd src/hash_wangwen
.\script3.ps1 -FolderPath "C:\path\to\files" -HashAlgorithm "SHA256"
```

---

**维护建议**: 定期检查各模块的依赖更新，特别是ffmpeg和pandas版本兼容性。

**文档更新**: 当添加新功能或修改现有功能时，更新此文档和模块内的README文件。

**代码审查**: 注意跨平台兼容性问题，特别是PowerShell脚本在非Windows环境下的运行。
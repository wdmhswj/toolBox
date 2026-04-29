# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

toolBox is a collection of independent utility tools with no shared code or centralized build system. Each module is self-contained. The project uses Chinese as the primary language for documentation and code comments.

## Module Architecture

### file_comparison/
Pure frontend HTML/JS/CSS tools - open directly in a browser, no server needed.
- file_compare.html - basic diff viewer
- file_compare_v2.html - advanced NexDiff viewer with dark theme, character-level diff, and export

### video_format_conversion/
Python video converter using FFmpeg as the backend. Three evolutionary versions:
- main.py - original, simple CLI and Tkinter GUI
- main_v2.py - adds batch conversion, preset management (JSON), progress/ETA, 3-tab GUI
- main_v3.py - latest; adds parallel batch processing (ThreadPoolExecutor), FFprobe-based progress, VideoInfo dataclass, Chinese UI labels

Running the converter:

  python video_format_conversion/main_v3.py
  python video_format_conversion/main.py --input video.mp4 --output out.mkv

Requirements: Python 3.7+, FFmpeg and FFprobe installed and on PATH. No requirements.txt exists; no extra pip packages needed beyond the standard library.

### src/PassswordGenenrator/
C++ password generator. Note the directory name has a typo (triple-s, extra e in Generator): PassswordGenenrator.

Two entry points:
- main.cpp - interactive mode
- pswdGenerator_cmd.cpp - CLI mode

Compile on Windows with MSVC:

  cl main.cpp PasswordGenerator.cpp -o PasswordGenerator.exe

Known issue: Uses std::rand() which is not cryptographically secure.

### src/hash_wangwen/
PowerShell scripts (Windows-only) that rename files to their SHA256/MD5 hash values.
- script.ps1 - basic, .txt files only
- script2.ps1 - multi-extension support with statistics
- script3.ps1 - full-featured: logging, file mapping report, comprehensive error handling

Run:

  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  .\src\hash_wangwen\script3.ps1

### src/name_xuehao/
Python script that reads student IDs from .xls files and queries an external API.

Dependencies (install manually):

  pip install pandas requests xlrd

Run:

  python src/name_xuehao/solution1.py

## Key Patterns

- No centralized build system, no requirements.txt, no Makefile - each module is fully independent.
- Version evolution is tracked in-repo (v1 to v2 to v3) rather than via git branches.
- Python modules use Tkinter for GUI; no web framework is used.
- .gitignore excludes: *.exe, *.o, *.txt, *.xls, __pycache__.

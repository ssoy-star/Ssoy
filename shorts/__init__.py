"""Ssoy shorts — 원본 영상을 쇼츠/릴스/틱톡용 세로 영상으로 자동 편집하는 도구."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = Path(__file__).resolve().parent
FONTS_DIR = PKG / "fonts"
STYLES_DIR = PKG / "styles"
WORK_DIR = ROOT / "work"
OUTPUT_DIR = ROOT / "output"
INBOX_DIR = ROOT / "inbox"

__version__ = "0.1.0"

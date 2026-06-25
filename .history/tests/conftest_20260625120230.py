"""pytest 配置。"""
import sys
from pathlib import Path

# 确保 src/ 在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

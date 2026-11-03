"""Записать версию в avtootchet/__init__.py: python tools/set_version.py 1.2.3"""
import re
import sys
from pathlib import Path

version = sys.argv[1].lstrip("v")
if not re.fullmatch(r"\d+(\.\d+){1,3}", version):
    sys.exit(f"Версия должна быть вида 1.2.3, а не «{sys.argv[1]}»")
p = Path(__file__).resolve().parents[1] / "avtootchet" / "__init__.py"
p.write_text(re.sub(r'__version__ = ".*"', f'__version__ = "{version}"', p.read_text(encoding="utf-8")),
             encoding="utf-8")
print("версия", version)

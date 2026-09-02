"""测试公共配置。

src-layout 项目在 `uv sync`(无 build-system)下不会被 pip 安装,
因此这里把 `backend/src` 提前插入 sys.path,使 `report_agent` 可被各测试导入。
后续任务的 mock fixtures 也统一放本文件。
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

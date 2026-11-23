"""启动器：显式把项目根目录加入 sys.path 后调用分析包。

配合 `python3 -I report.py` 使用：隔离模式下解释器不会自动加入
脚本目录，这里显式加入项目根（不含数据目录），避免记录目录中的
文件被当作模块加载。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis.cli import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())

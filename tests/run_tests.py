"""无头测试运行器：用 AstrBot 嵌入式 Python 3.12 跑全部单测。

用法（项目根目录）：
  D:\\AstrBot\\backend\\python\\python.exe tests\\run_tests.py
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = r"C:\Users\miku\.astrbot\data\site-packages"

for p in (ROOT, SITE):
    if p not in sys.path:
        sys.path.insert(0, p)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(
        os.path.join(ROOT, "tests"), pattern="test_*.py"
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)

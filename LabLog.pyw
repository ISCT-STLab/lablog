"""LabLog を起動する（ダブルクリックで、コンソール窓なしで起動）。

すでに起動している場合は、ダッシュボードをブラウザで開くだけになる。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lablog.main import main  # noqa: E402

main()

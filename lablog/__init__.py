"""LabLog: 研究室の入退室と PC 作業をローカルだけで記録するアプリ。"""

__version__ = "0.9.0"

# 配布元（研究室の GitHub Organization）。更新の確認とブラウザ版の公開に使う。
GITHUB_OWNER = "ISCT-STLab"
GITHUB_REPO = f"{GITHUB_OWNER}/lablog"
# ブラウザ版（スマホ用ビューアー）。テスト版は beta/ に置く。
VIEWER_URL = f"https://{GITHUB_OWNER.lower()}.github.io/lablog/"
VIEWER_BETA_URL = VIEWER_URL + "beta/"

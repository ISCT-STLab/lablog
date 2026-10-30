"""LabLog の起動用（setup.ps1 が、インストール先に LabLog.pyw という名前で置く）。

version.json に書いてあるバージョンのフォルダ（versions\\<バージョン>）から LabLog を起動する。
更新したあとのバージョンが起動の途中で失敗したら、前のバージョンに戻して起動し直す（戻すのは 1 回だけ）。
どのバージョンの LabLog にも頼らずに動くよう、標準ライブラリだけで書く（lablog/install.py と同じ形を読む）。
"""

import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
VERSION_FILE = BASE / "version.json"


def log(message: str):
    with open(BASE / "launcher.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")


def main():
    info = json.loads(VERSION_FILE.read_text(encoding="utf-8-sig"))
    current = info["current"]
    sys.path.insert(0, str(BASE / "versions" / current))
    try:
        from lablog.main import main as run
        run(sys.argv[1:])
    except Exception:
        log(f"{current} を起動できませんでした:\n{traceback.format_exc()}")
        previous = info.get("previous")
        if not previous or previous == current or not (BASE / "versions" / previous).is_dir():
            raise
        tmp = VERSION_FILE.with_name(VERSION_FILE.name + ".tmp")
        tmp.write_text(json.dumps({"current": previous, "previous": None, "failed": current}), encoding="utf-8")
        os.replace(tmp, VERSION_FILE)
        log(f"前のバージョン（{previous}）に戻して起動し直します")
        # このプロセスが二重起動防止の印を持ったままなので、終わるのを待ってから起動してもらう（--after）
        rest = sys.argv[1:]
        args = [a for i, a in enumerate(rest) if a != "--after" and (i == 0 or rest[i - 1] != "--after")]
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *args, "--after", str(os.getpid())],
                         cwd=str(BASE))


if __name__ == "__main__":
    main()

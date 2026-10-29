"""配布用の ZIP を作る（GitHub の Releases に置くもの）。

アプリを動かすのに必要なファイルだけを入れる。テスト・開発用の道具・記録のデータは入れない。
ZIP の中は LabLog-<版>/ フォルダ 1 つにまとめる（展開したときに散らばらないように）。

  python tools/release.py            → dist/LabLog-<版>.zip と、その SHA-256
"""

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lablog import __version__  # noqa: E402

# 入れるもの（ROOT からの相対パス）。フォルダは中身ごと入れる。
# install_autostart.ps1 などは開発用（リポジトリのフォルダから動かす）なので入れない
INCLUDE = ["setup.cmd", "setup.ps1", "uninstall.cmd", "uninstall.ps1", "launcher.pyw", "LabLog.pyw", "README.md",
           "LICENSE", "lablog"]
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def release_files() -> list[Path]:
    files = []
    for name in INCLUDE:
        path = ROOT / name
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files += sorted(p for p in path.rglob("*") if p.is_file()
                            and not SKIP_DIRS & set(p.relative_to(ROOT).parts) and p.suffix not in SKIP_SUFFIXES)
    return files


def build(out_dir: Path, version: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"LabLog-{version}.zip"
    top = f"LabLog-{version}"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for path in release_files():
            z.write(path, f"{top}/{path.relative_to(ROOT).as_posix()}")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "dist"), help="ZIP を置くフォルダ")
    args = parser.parse_args()

    missing = [name for name in INCLUDE if not (ROOT / name).exists()]
    if missing:
        print("見つからないので入れません: " + ", ".join(missing))
    target = build(Path(args.out), __version__)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    with zipfile.ZipFile(target) as z:
        count = len(z.namelist())
    print(f"作りました: {target}（{count} ファイル、{target.stat().st_size / 1024:.0f} KB）")
    print(f"SHA-256: {digest}")


if __name__ == "__main__":
    main()

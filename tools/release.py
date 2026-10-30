"""配布用の ZIP を作る（GitHub の Releases に置くもの）。

アプリを動かすのに必要なファイルだけを入れる。テスト・開発用の道具・記録のデータは入れない。
ZIP の中は LabLog-<バージョン>/ フォルダ 1 つにまとめる（展開したときに散らばらないように）。
CHANGELOG.md からそのバージョンの変更点を取り出して、リリースの説明に使うファイルも作る。

  python tools/release.py            → dist/LabLog-<バージョン>.zip と、その SHA-256、dist/LabLog-<バージョン>-notes.md
"""

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lablog import __version__  # noqa: E402

# 入れるもの（ROOT からの相対パス）。フォルダは中身ごと入れる。
# install_autostart.ps1 などは開発用（リポジトリのフォルダから動かす）なので入れない
INCLUDE = ["setup.cmd", "setup.ps1", "uninstall.cmd", "uninstall.ps1", "launcher.pyw", "LabLog.pyw", "README.md",
           "CHANGELOG.md", "LICENSE", "lablog"]
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


def release_notes(changelog: str, version: str) -> str | None:
    """CHANGELOG.md の「## <バージョン>」の節（次の「## 」の手前まで）。"""
    m = re.search(rf"^## {re.escape(version)}\s*$\n(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    return m.group(1).strip() if m else None


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
    notes = release_notes((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), __version__)
    if notes:
        notes_path = target.with_name(f"LabLog-{__version__}-notes.md")
        notes_path.write_text(notes + "\n", encoding="utf-8")
        print(f"リリースの説明: {notes_path}")
    else:
        print(f"CHANGELOG.md に「## {__version__}」の節がありません。リリースの前に書いてください")


if __name__ == "__main__":
    main()

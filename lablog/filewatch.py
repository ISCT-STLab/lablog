"""登録フォルダ内のファイル変更を監視する（ReadDirectoryChangesW）。

ファイルの中身は読まず、パス・時刻・操作・サイズだけを記録する。
"""

import ctypes
import ctypes.wintypes as wt
import logging
import os
import re
import struct
import threading
import time

log = logging.getLogger(__name__)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.ReadDirectoryChangesW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, wt.BOOL, wt.DWORD,
                                           ctypes.POINTER(wt.DWORD), ctypes.c_void_p, ctypes.c_void_p]
kernel32.ReadDirectoryChangesW.restype = wt.BOOL
kernel32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

FILE_LIST_DIRECTORY = 0x0001
FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
INVALID_HANDLE_VALUE = wt.HANDLE(-1).value
NOTIFY_FILTER = 0x1 | 0x2 | 0x10  # ファイル名・フォルダ名の変更、最終書き込み
ACTIONS = {1: "added", 2: "removed", 3: "modified", 4: "renamed_from", 5: "renamed_to"}

IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".ipynb_checkpoints", ".idea",
               ".vscode", ".pytest_cache", ".mypy_cache", ".ruff_cache", "$recycle.bin", ".tmp.drivedownload",
               ".tmp.driveupload",
               # ホームフォルダごと監視したときに、アプリが裏で書き続けるもの
               "appdata", ".cache", ".npm", ".nuget", ".gradle", ".m2", ".cargo", ".rustup", ".conda", ".ollama",
               ".local"}
IGNORE_SUFFIXES = (".tmp", ".temp", ".swp", ".swx", "~", ".crdownload", ".partial", ".lock", ".lck",
                   ".pyc", ".aux.bak", "-journal", "-wal", "-shm")
IGNORE_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
# Office が保存時に使う一時ファイル（拡張子なしの 8 桁 16 進数）
_OFFICE_TEMP = re.compile(r"^[0-9A-F]{8}$")
DEDUP_SEC = 5


def is_ignored(rel_path: str) -> bool:
    parts = rel_path.replace("/", "\\").split("\\")
    if any(p.lower() in IGNORE_DIRS for p in parts[:-1]):
        return True
    name = parts[-1].lower()
    return (name in IGNORE_NAMES or name.startswith("~$") or name.startswith(".~lock")
            or name.endswith(IGNORE_SUFFIXES) or bool(_OFFICE_TEMP.match(parts[-1]))
            or parts[-1].lower() in IGNORE_DIRS)


class FileEventSink:
    """複数の監視スレッドから受け取ったイベントを、重複を除いて DB に書く。"""

    def __init__(self, conn, is_paused, exclude_prefixes: list[str]):
        self.conn = conn
        self.is_paused = is_paused
        self.exclude = [os.path.normcase(p) for p in exclude_prefixes]
        self._lock = threading.Lock()
        self._recent: dict[str, float] = {}
        self._pending_rename: dict[str, str] = {}

    def handle(self, folder: str, rel: str, action: str):
        if self.is_paused() or is_ignored(rel):
            return
        path = os.path.join(folder, rel)
        if any(os.path.normcase(path).startswith(p) for p in self.exclude):
            return
        now = time.time()
        with self._lock:
            if action == "renamed_from":
                self._pending_rename[folder] = path
                return
            old_path = None
            if action == "renamed_to":
                old_path = self._pending_rename.pop(folder, None)
                action = "renamed"
            if action in ("added", "modified", "renamed"):
                if os.path.isdir(path):
                    return
                if action == "modified":
                    last = self._recent.get(path)
                    if last is not None and now - last < DEDUP_SEC:
                        return
                self._recent[path] = now
            if len(self._recent) > 5000:
                cutoff = now - DEDUP_SEC
                self._recent = {k: v for k, v in self._recent.items() if v >= cutoff}
            try:
                size = os.path.getsize(path) if action != "removed" else None
            except OSError:
                size = None
            try:
                self.conn.execute("INSERT INTO file_events (ts, folder, path, action, size, old_path) "
                                  "VALUES (?, ?, ?, ?, ?, ?)", (now, folder, path, action, size, old_path))
            except Exception:
                log.exception("ファイルイベントを保存できませんでした")


class FolderWatcher(threading.Thread):
    def __init__(self, folder: str, sink: FileEventSink):
        super().__init__(daemon=True, name=f"watch:{folder}")
        self.folder = folder
        self.sink = sink
        self._stop = threading.Event()
        self._handle = None

    def run(self):
        while not self._stop.is_set():
            self._handle = kernel32.CreateFileW(self.folder, FILE_LIST_DIRECTORY, FILE_SHARE_ALL, None,
                                                OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
            if not self._handle or self._handle == INVALID_HANDLE_VALUE:
                log.warning("フォルダを監視できません（%s）。1 分後に再試行します", self.folder)
                self._stop.wait(60)
                continue
            try:
                self._loop()
            finally:
                kernel32.CloseHandle(self._handle)
                self._handle = None
            self._stop.wait(5)

    def _loop(self):
        buf = ctypes.create_string_buffer(64 * 1024)
        returned = wt.DWORD()
        while not self._stop.is_set():
            ok = kernel32.ReadDirectoryChangesW(self._handle, buf, len(buf), True, NOTIFY_FILTER,
                                                ctypes.byref(returned), None, None)
            if not ok:
                return
            if returned.value == 0:
                continue  # 変更が多すぎてバッファがあふれた。取りこぼしは諦める。
            data = buf.raw[:returned.value]
            offset = 0
            while True:
                next_offset, action, name_len = struct.unpack_from("<III", data, offset)
                name = data[offset + 12: offset + 12 + name_len].decode("utf-16-le", errors="replace")
                if action in ACTIONS:
                    self.sink.handle(self.folder, name, ACTIONS[action])
                if next_offset == 0:
                    break
                offset += next_offset

    def stop(self):
        self._stop.set()
        if self._handle:
            kernel32.CancelIoEx(self._handle, None)


class WatcherManager:
    def __init__(self, sink: FileEventSink):
        self.sink = sink
        self.watchers: dict[str, FolderWatcher] = {}
        self._lock = threading.Lock()

    def sync(self, folders: list[str]):
        wanted = {os.path.normpath(f) for f in folders if os.path.isdir(f)}
        with self._lock:
            for folder in list(self.watchers):
                if folder not in wanted:
                    self.watchers.pop(folder).stop()
            for folder in wanted - set(self.watchers):
                watcher = FolderWatcher(folder, self.sink)
                watcher.start()
                self.watchers[folder] = watcher

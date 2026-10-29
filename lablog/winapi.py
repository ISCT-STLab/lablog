"""Windows API の薄いラッパー（標準ライブラリの ctypes だけで実装）。"""

import base64
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
from xml.sax.saxutils import escape

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

CREATE_NO_WINDOW = 0x08000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ALREADY_EXISTS = 183

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.EnumChildWindows.argtypes = [wt.HWND, WNDENUMPROC, wt.LPARAM]
user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
kernel32.GetTickCount64.restype = ctypes.c_ulonglong
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int,
                         ctypes.c_int, wt.DWORD]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]

DPI_AWARENESS_PER_MONITOR_V2 = ctypes.c_void_p(-4)
DWMWA_EXTENDED_FRAME_BOUNDS = 9
SRCCOPY = 0x00CC0020


def idle_seconds() -> float:
    """最後にキーボード・マウスを操作してからの秒数。"""
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    now = kernel32.GetTickCount64() & 0xFFFFFFFF
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


def total_memory_gb() -> float:
    """この PC のメモリの大きさ（GB）。分からなければ 0。"""
    status = MEMORYSTATUSEX(ctypes.sizeof(MEMORYSTATUSEX))
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 0.0
    return status.ullTotalPhys / 1024 ** 3


SM_REMOTESESSION = 0x1000


def is_remote_session() -> bool:
    """Windows のリモートデスクトップで操作されているか（このプロセスのセッションがリモートか）。

    Chrome リモートデスクトップなど、画面をそのまま映すソフトは手元のセッションのままなので見分けられない。
    """
    return bool(user32.GetSystemMetrics(SM_REMOTESESSION))


def _exe_of_pid(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _uwp_child_exe(hwnd, frame_pid: int) -> str:
    """ストアアプリは ApplicationFrameHost.exe に包まれているので、中身のプロセスを探す。"""
    found = []

    @WNDENUMPROC
    def callback(child, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(child, ctypes.byref(pid))
        if pid.value != frame_pid:
            found.append(pid.value)
            return False
        return True

    user32.EnumChildWindows(hwnd, callback, 0)
    return _exe_of_pid(found[0]) if found else ""


def foreground_window() -> tuple[str, str] | None:
    """前面ウィンドウの (実行ファイル名, タイトル)。"""
    hwnd = user32.GetForegroundWindow()
    return window_info(hwnd) if hwnd else None


def foreground_handle():
    return user32.GetForegroundWindow()


def window_info(hwnd) -> tuple[str, str]:
    """ウィンドウの (実行ファイル名, タイトル)。"""
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = _exe_of_pid(pid.value)
    if exe.lower() == "applicationframehost.exe":
        exe = _uwp_child_exe(hwnd, pid.value) or exe
    return exe, buf.value


def capture_window(hwnd) -> tuple[int, int, bytes] | None:
    """ウィンドウに表示されている部分を画面から写し取る（幅, 高さ, BGRA の画素・上から下）。

    前面にあるウィンドウにだけ使う。結果はメモリ上だけで扱い、ファイルには保存しない。
    """
    if not hwnd or user32.IsIconic(hwnd):
        return None
    # 拡大表示（125% など）の画面でも、実際の画素の大きさで位置を取る
    previous = user32.SetThreadDpiAwarenessContext(DPI_AWARENESS_PER_MONITOR_V2)
    screen = mem = bitmap = old = None
    try:
        rect = wt.RECT()
        if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect),
                                        ctypes.sizeof(rect)) != 0:
            user32.GetWindowRect(hwnd, ctypes.byref(rect))  # 影を含む少し大きい範囲になる
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 100 or height < 100 or width > 10000 or height > 10000:
            return None
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
        old = gdi32.SelectObject(mem, bitmap)
        if not gdi32.BitBlt(mem, 0, 0, width, height, screen, rect.left, rect.top, SRCCOPY):
            return None
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(mem, bitmap, 0, height, pixels, ctypes.byref(header), 0):
            return None
        return width, height, pixels.raw
    finally:
        if old:
            gdi32.SelectObject(mem, old)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if mem:
            gdi32.DeleteDC(mem)
        if screen:
            user32.ReleaseDC(None, screen)
        if previous:
            user32.SetThreadDpiAwarenessContext(previous)


SYNCHRONIZE = 0x00100000
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]


def wait_for_process(pid: int, timeout_sec: float):
    """プロセスが終わるのを待つ（更新して起動し直すとき、前のプロセスが二重起動防止の印を手放すまで）。"""
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:  # もう終わっている
        return
    try:
        kernel32.WaitForSingleObject(handle, int(timeout_sec * 1000))
    finally:
        kernel32.CloseHandle(handle)


def acquire_single_instance(name: str = "Local\\LabLog-Tracker"):
    """二重起動を防ぐ。すでに起動していれば None を返す。"""
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def run_quiet(args: list[str], timeout: float = 30, encoding: str = "utf-8", cwd=None, env=None):
    """コンソール窓を出さずにコマンドを実行する。"""
    return subprocess.run(args, capture_output=True, text=True, encoding=encoding, errors="replace",
                          timeout=timeout, cwd=cwd, env=env, creationflags=CREATE_NO_WINDOW,
                          stdin=subprocess.DEVNULL)


# Windows PowerShell の AppUserModelID。これを使うと追加登録なしでトースト通知を出せる。
_POWERSHELL_AUMID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


def show_toast(title: str, body: str, launch_url: str | None = None):
    launch = f' activationType="protocol" launch="{escape(launch_url, {chr(34): "&quot;"})}"' if launch_url else ""
    xml = (f"<toast{launch}><visual><binding template=\"ToastGeneric\">"
           f"<text>{escape(title)}</text><text>{escape(body)}</text></binding></visual></toast>")
    xml_ps = xml.replace("'", "''")
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n"
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        f"$xml.LoadXml('{xml_ps}')\n"
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml\n"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_POWERSHELL_AUMID}').Show($toast)\n"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                      "-EncodedCommand", encoded], creationflags=CREATE_NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

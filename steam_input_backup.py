#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Steam Input 配置备份管理工具
- 监控 Steam 控制器配置目录
- 按游戏（AppID / 非 Steam 游戏名）自动分类备份
- 列表优先显示 Steam 前端游戏名，AppID 放在后面
- 立即存档：把 Steam 里当前全部 VDF 按时间戳保存到软件目录下的「存档」文件夹，不删除已有文件
- 恢复：选择一个游戏，把存档复制回该游戏原来的 Steam 目录
- 可在软件内把一个游戏的 VDF 迁移到另一个游戏
- 图形界面 + 系统托盘
"""

import os
import sys
import re
import json
import time
import struct
import shutil
import hashlib
import threading
import ctypes
import winreg
from ctypes import wintypes
from pathlib import Path
from datetime import datetime
from tkinter import (
    Tk, Toplevel, Listbox, END, SINGLE, ttk, messagebox, filedialog,
    StringVar, IntVar, BooleanVar, Menu,
)
from tkinter.scrolledtext import ScrolledText
import pystray
from PIL import Image, ImageDraw

# ==================== 配置与常量 ====================
APP_NAME = "SteamInputBackup"
CONFIG_FILE = Path.home() / "AppData" / "Roaming" / APP_NAME / "config.json"
TIMESTAMP_FMT = "%Y%m%d_%H%M%S"
SNAPSHOT_DIR_RE = re.compile(r"^\d{8}_\d{6}(?:_\d+)?$")
OLD_DOCUMENTS_BACKUP_DIR = Path.home() / "Documents" / "SteamInputBackups"


def get_app_dir() -> Path:
    """软件所在目录：打包后是 exe 旁边，开发时是脚本旁边。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


DEFAULT_BACKUP_DIR = get_app_dir() / "存档"

# 单实例：同名互斥体，第二次启动只激活已打开的窗口
WINDOW_TITLE = "Steam Input 配置备份管理器"
SINGLE_INSTANCE_MUTEX = "Local\\SteamInputBackup_SingleInstance"
ACTIVATE_EVENT_NAME = "Local\\SteamInputBackup_Activate"
ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
EVENT_MODIFY_STATE = 0x0002
SW_RESTORE = 9
SW_SHOW = 5

_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_KERNEL32.CreateMutexW.restype = wintypes.HANDLE
_KERNEL32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_KERNEL32.CreateEventW.restype = wintypes.HANDLE
_KERNEL32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_KERNEL32.OpenEventW.restype = wintypes.HANDLE
_KERNEL32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_KERNEL32.SetEvent.argtypes = [wintypes.HANDLE]
_KERNEL32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_KERNEL32.WaitForSingleObject.restype = wintypes.DWORD
_KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
_USER32.FindWindowW.restype = wintypes.HWND
_USER32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_USER32.IsIconic.argtypes = [wintypes.HWND]
_USER32.IsIconic.restype = wintypes.BOOL
_USER32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_USER32.SetForegroundWindow.argtypes = [wintypes.HWND]
_USER32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]


def _bring_existing_window_to_front():
    ev = _KERNEL32.OpenEventW(EVENT_MODIFY_STATE, False, ACTIVATE_EVENT_NAME)
    if ev:
        _KERNEL32.SetEvent(ev)
        _KERNEL32.CloseHandle(ev)
    hwnd = _USER32.FindWindowW(None, WINDOW_TITLE)
    if hwnd:
        if _USER32.IsIconic(hwnd):
            _USER32.ShowWindow(hwnd, SW_RESTORE)
        _USER32.ShowWindow(hwnd, SW_SHOW)
        _USER32.SetForegroundWindow(hwnd)


def acquire_single_instance():
    """已有实例在跑则激活它并返回 None，否则返回需保持到退出的 (mutex, event)。"""
    event = _KERNEL32.CreateEventW(None, False, False, ACTIVATE_EVENT_NAME)
    mutex = _KERNEL32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _bring_existing_window_to_front()
        _USER32.MessageBoxW(None, "程序已经在运行，已切换到已打开的窗口。", WINDOW_TITLE, 0x40)
        if mutex:
            _KERNEL32.CloseHandle(mutex)
        if event:
            _KERNEL32.CloseHandle(event)
        return None
    return mutex, event


def start_activate_watcher(app, event_handle):
    def loop():
        while getattr(app, "running", False):
            rc = _KERNEL32.WaitForSingleObject(event_handle, 500)
            if rc == WAIT_OBJECT_0 and getattr(app, "running", False):
                app.root.after(0, app.show_from_tray)
    threading.Thread(target=loop, daemon=True).start()


# 常见 Steam 路径检测
COMMON_STEAM_PATHS = [
    Path(r"C:\Program Files (x86)\Steam"),
    Path(r"C:\Program Files\Steam"),
    Path(r"D:\Steam"),
    Path(r"E:\Steam"),
]


def get_steam_path_from_registry():
    """从注册表读取 Steam 安装路径"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            path, _ = winreg.QueryValueEx(key, "SteamPath")
            return Path(path)
    except Exception:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam") as key:
            path, _ = winreg.QueryValueEx(key, "InstallPath")
            return Path(path)
    except Exception:
        pass
    return None


def detect_steam_path():
    reg = get_steam_path_from_registry()
    if reg and reg.exists():
        return reg
    for p in COMMON_STEAM_PATHS:
        if p.exists() and (p / "steam.exe").exists():
            return p
    return None


def get_steam_language():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            lang, _ = winreg.QueryValueEx(key, "Language")
            return str(lang).lower()
    except Exception:
        return "english"


def file_hash(path: Path, chunk=8192):
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk)
                if not data:
                    break
                h.update(data)
        return h.hexdigest()
    except Exception:
        return None


def is_appid(name: str) -> bool:
    return bool(re.fullmatch(r"\d+", str(name)))


def iter_library_steamapps(steam: Path):
    libs = [steam / "steamapps"]
    for vdf_path in (
        steam / "steamapps" / "libraryfolders.vdf",
        steam / "config" / "libraryfolders.vdf",
    ):
        if not vdf_path.exists():
            continue
        try:
            text = vdf_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            raw = m.group(1).replace("\\\\", "\\")
            libs.append(Path(raw) / "steamapps")

    seen = set()
    out = []
    for lib in libs:
        try:
            key = str(lib.resolve()).lower()
        except Exception:
            key = str(lib).lower()
        if key not in seen and lib.exists():
            seen.add(key)
            out.append(lib)
    return out


def parse_manifest_names(steam: Path) -> dict:
    names = {}
    for lib in iter_library_steamapps(steam):
        for acf in lib.glob("appmanifest_*.acf"):
            try:
                text = acf.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            m_id = re.search(r'"appid"\s+"(\d+)"', text)
            m_name = re.search(r'"name"\s+"([^"]+)"', text)
            if m_id and m_name:
                title = m_name.group(1).strip()
                if title:
                    names[m_id.group(1)] = title
    return names


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def _has_kana(s: str) -> bool:
    return any("\u3040" <= c <= "\u30ff" for c in s)


def _is_title_like(s: str) -> bool:
    s = s.strip()
    if not (2 <= len(s) <= 80):
        return False
    if s.startswith("#") or s.startswith("http"):
        return False
    lower = s.lower()
    if lower.endswith((".jpg", ".png", ".svg", ".vdf", ".gif")):
        return False
    if s in {"Game", "game", "windows", "released", "macos", "linux"}:
        return False
    return True


def _score_localized(s: str, lang: str):
    tchinese_marks = "門東語體龍後臺賣麼與點開發戰條"
    schinese_marks = "门东语体龙后台卖么与点开发战条"
    tc = sum(c in tchinese_marks for c in s)
    sc = sum(c in schinese_marks for c in s)
    if lang.startswith("tchinese"):
        return (sc, len(s))
    return (tc, len(s))


def parse_appinfo_names(steam: Path, lang: str = "english") -> dict:
    path = steam / "appcache" / "appinfo.vdf"
    if not path.exists():
        return {}
    try:
        data = path.read_bytes()
    except Exception:
        return {}

    needle = b"\x00\x03\x00\x00\x00\x01\x04\x00\x00\x00"
    names = {}
    records = []
    start = 0
    while True:
        i = data.find(needle, start)
        if i < 0:
            break
        if i >= 8 and data[i - 8:i - 4] == b"\x01\x00\x00\x00":
            appid = struct.unpack_from("<I", data, i - 4)[0]
            ns = i + len(needle)
            ne = data.find(b"\x00", ns)
            if ne != -1 and appid:
                name = data[ns:ne].decode("utf-8", errors="ignore").strip()
                if name:
                    names[str(appid)] = name
                    records.append((ns, ne, str(appid), name))
        start = i + 1

    prefer_cjk = lang.startswith("schinese") or lang.startswith("tchinese")
    if prefer_cjk and records:
        next_starts = [r[0] for r in records] + [len(data)]
        for idx, (ns, ne, appid, official) in enumerate(records):
            if _has_cjk(official) and not _has_kana(official):
                continue
            end = min(next_starts[idx + 1], ne + 8000)
            chunk = data[ne:end]
            candidates = []
            cur = bytearray()
            for b in chunk:
                if b != 0:
                    cur.append(b)
                else:
                    if 2 <= len(cur) <= 80:
                        try:
                            s = cur.decode("utf-8").strip()
                        except Exception:
                            s = ""
                        if (
                            s
                            and _has_cjk(s)
                            and not _has_kana(s)
                            and _is_title_like(s)
                        ):
                            candidates.append(s)
                    cur = bytearray()
            if candidates:
                names[appid] = sorted(candidates, key=lambda s: _score_localized(s, lang))[0]
    return names


def build_appid_name_map(steam_path) -> dict:
    if not steam_path:
        return {}
    steam = Path(steam_path)
    if not steam.exists():
        return {}
    lang = get_steam_language()
    names = parse_appinfo_names(steam, lang)
    for appid, title in parse_manifest_names(steam).items():
        names.setdefault(appid, title)
    return names


def format_game_label(folder_name: str, name_map: dict) -> str:
    if is_appid(folder_name):
        title = name_map.get(folder_name)
        if title and title != folder_name:
            return f"{title}  ({folder_name})"
    return folder_name


# ==================== 主应用 ====================

def retarget_controller_vdf(text: str, source_game: str, target_game: str, dest: Path) -> str:
    """把布局里的 usercloud / autosave 路径改到目标游戏，否则 Steam 仍会把它算在原游戏下。"""
    def repl_usercloud(m):
        rest = m.group(1)
        layout = rest.split("/", 1)[-1] if rest else dest.stem
        return f'"usercloud://{target_game}/{layout}"'

    text = re.sub(r'"usercloud://([^"]*)"', repl_usercloud, text)
    dest_autosave = str(dest).replace("\\", "\\\\")
    text = re.sub(r'"autosave://[^"]*"', f'"autosave://{dest_autosave}"', text)
    return text


def detect_controller_type(vdf_text: str) -> str:
    m = re.search(r'"controller_type"\s+"([^"]+)"', vdf_text)
    return m.group(1) if m else ""



class SteamInputBackupApp:
    def __init__(self):
        self.root = Tk()
        self.root.title(WINDOW_TITLE)
        self.root.geometry("1080x640")
        self.root.minsize(800, 500)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.settings = self.load_settings()
        self.backup_thread = None
        self.running = True
        self.tray_icon = None
        self.appid_names = {}

        self.build_ui()

        # 先补齐 Steam 路径，刷新列表时才能解析游戏名
        if not self.settings.get("steam_path"):
            auto = detect_steam_path()
            if auto:
                self.settings["steam_path"] = str(auto)
                self.save_settings()

        # 启动只刷新软件同目录下的存档，不扫描 Steam
        self.refresh_tree()

    # ---------- 设置 ----------
    def load_settings(self):
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        default = {
            "steam_path": "",
            "backup_dir": str(DEFAULT_BACKUP_DIR),
            "interval_minutes": 5,
            "max_backups": 20,
            "auto_backup": False,
            "monitor_common": True,
            "monitor_userdata": True,
        }
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                default.update(data)
            except Exception:
                pass
        # 存档固定在软件同目录下的「存档」，启动时只读这里，不读文档或其他位置
        default["backup_dir"] = str(DEFAULT_BACKUP_DIR)
        default["auto_backup"] = False
        return default

    def save_settings(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            messagebox.showerror("错误", f"保存设置失败: {e}")

    # ---------- UI ----------
    def build_ui(self):
        # 顶部工具栏
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill="x", padx=8, pady=6)

        ttk.Button(toolbar, text="立即存档", command=self.manual_backup).pack(side="left", padx=4)
        ttk.Button(toolbar, text="刷新列表", command=self.refresh_tree).pack(side="left", padx=4)
        ttk.Button(toolbar, text="恢复到 Steam", command=self.restore_selected).pack(side="left", padx=4)
        ttk.Button(toolbar, text="迁移 VDF", command=self.migrate_selected).pack(side="left", padx=4)
        ttk.Button(toolbar, text="只保留最近存档", command=self.keep_latest_archive).pack(side="left", padx=4)
        ttk.Button(toolbar, text="打开原 VDF 目录", command=self.open_live_game_folder).pack(side="left", padx=4)
        ttk.Button(toolbar, text="设置", command=self.open_settings).pack(side="left", padx=4)
        ttk.Button(toolbar, text="隐藏到托盘", command=self.hide_to_tray).pack(side="right", padx=4)

        # 主区域：树形列表
        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=8, pady=4)

        columns = ("time", "size", "path")
        self.tree = ttk.Treeview(frame, columns=columns, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="游戏 / 配置文件")
        self.tree.heading("time", text="备份时间")
        self.tree.heading("size", text="大小")
        self.tree.heading("path", text="原始路径")

        self.tree.column("#0", width=360)
        self.tree.column("time", width=160)
        self.tree.column("size", width=90)
        self.tree.column("path", width=400)

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        # 状态栏
        self.status_var = StringVar(value="就绪")
        status = ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w")
        status.pack(fill="x", side="bottom")

        # 右键菜单
        self.menu = Menu(self.root, tearoff=0)
        self.menu.add_command(label="恢复到原游戏目录", command=self.restore_selected)
        self.menu.add_command(label="迁移到其他游戏…", command=self.migrate_selected)
        self.menu.add_command(label="打开原 VDF 文件夹", command=self.open_live_game_folder)
        self.menu.add_command(label="打开备份所在文件夹", command=self.open_backup_folder)
        self.tree.bind("<Button-3>", self.show_context_menu)

    def show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.menu.post(event.x_root, event.y_root)

    def game_label(self, folder_name: str) -> str:
        return format_game_label(folder_name, self.appid_names)

    def collect_known_games(self):
        """收集备份目录和 Steam 配置目录中的游戏文件夹名。"""
        keys = set()
        backup_root = Path(self.settings.get("backup_dir") or "")
        if backup_root.exists():
            for p in backup_root.iterdir():
                if p.is_dir():
                    keys.add(p.name)
        for root in self.get_config_roots():
            try:
                for p in root.iterdir():
                    if p.is_dir():
                        keys.add(p.name)
            except Exception:
                pass
        return keys

    # ---------- 备份逻辑 ----------
    def get_config_roots(self):
        """返回所有需要监控的配置根目录列表 [(game_or_appid, Path), ...] 的父级"""
        roots = []
        steam = Path(self.settings.get("steam_path") or "")
        if not steam.exists():
            return roots

        # 1. steamapps/common/Steam Controller Configs/<userid>/config/
        if self.settings.get("monitor_common", True):
            base = steam / "steamapps" / "common" / "Steam Controller Configs"
            if base.exists():
                for userid in base.iterdir():
                    if userid.is_dir():
                        cfg = userid / "config"
                        if cfg.exists():
                            roots.append(cfg)

        # 2. userdata/<userid>/241100/remote/controller_config/
        if self.settings.get("monitor_userdata", True):
            userdata = steam / "userdata"
            if userdata.exists():
                for userid in userdata.iterdir():
                    if userid.is_dir():
                        cfg = userid / "241100" / "remote" / "controller_config"
                        if cfg.exists():
                            roots.append(cfg)
        return roots

    def scan_and_backup(self, force=False):
        """扫描并备份有变化的 .vdf 文件"""
        backup_root = Path(self.settings["backup_dir"])
        backup_root.mkdir(parents=True, exist_ok=True)
        backed = 0

        for config_root in self.get_config_roots():
            # config_root 下是各个游戏文件夹（AppID 或游戏名）
            for game_dir in config_root.iterdir():
                if not game_dir.is_dir():
                    continue
                game_name = game_dir.name  # AppID 或非 Steam 游戏名
                game_backup_dir = backup_root / game_name
                game_backup_dir.mkdir(exist_ok=True)

                for vdf in game_dir.rglob("*.vdf"):
                    if not vdf.is_file():
                        continue
                    try:
                        orig_hash = file_hash(vdf)
                        if not orig_hash:
                            continue

                        # 查找该文件的最新备份
                        stem = vdf.stem
                        pattern = f"{stem}_*.vdf"
                        existing = sorted(game_backup_dir.glob(pattern), reverse=True)
                        latest = existing[0] if existing else None

                        need_backup = force
                        if latest and not force:
                            latest_hash = file_hash(latest)
                            if latest_hash == orig_hash:
                                need_backup = False
                        else:
                            need_backup = True

                        if need_backup:
                            ts = datetime.now().strftime(TIMESTAMP_FMT)
                            dest_name = f"{stem}_{ts}.vdf"
                            dest = game_backup_dir / dest_name
                            # 防止同秒冲突
                            i = 1
                            while dest.exists():
                                dest = game_backup_dir / f"{stem}_{ts}_{i}.vdf"
                                i += 1
                            shutil.copy2(vdf, dest)
                            # 写入一个简单的 sidecar 记录原始路径（可选）
                            meta = dest.with_suffix(".meta.json")
                            with open(meta, "w", encoding="utf-8") as mf:
                                json.dump({
                                    "original": str(vdf),
                                    "game": game_name,
                                    "timestamp": ts,
                                    "hash": orig_hash
                                }, mf, ensure_ascii=False)
                            backed += 1

                    except Exception as e:
                        print(f"备份失败 {vdf}: {e}")

        return backed

    def backup_loop(self):
        while self.running:
            if self.settings.get("auto_backup", True):
                try:
                    n = self.scan_and_backup()
                    if n > 0:
                        self.root.after(0, lambda: self.status_var.set(f"自动备份完成，新增 {n} 个文件"))
                        self.root.after(0, self.refresh_tree)
                except Exception as e:
                    print("备份循环异常:", e)
            interval = max(1, int(self.settings.get("interval_minutes", 5))) * 60
            # 分段 sleep 以便快速退出
            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)

    def start_backup_thread(self):
        if self.backup_thread and self.backup_thread.is_alive():
            return
        self.backup_thread = threading.Thread(target=self.backup_loop, daemon=True)
        self.backup_thread.start()

    def archive_all(self):
        """把 Steam 里当前全部 VDF 存到软件存档目录，目录名带统一时间戳。不删除任何已有文件。"""
        self.settings["backup_dir"] = str(DEFAULT_BACKUP_DIR)
        backup_root = Path(self.settings["backup_dir"])
        backup_root.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime(TIMESTAMP_FMT)
        snap = backup_root / ts
        n = 1
        while snap.exists():
            snap = backup_root / f"{ts}_{n}"
            n += 1

        backed = 0
        for config_root in self.get_config_roots():
            for game_dir in config_root.iterdir():
                if not game_dir.is_dir():
                    continue
                game_name = game_dir.name
                for vdf in game_dir.rglob("*.vdf"):
                    if not vdf.is_file():
                        continue
                    try:
                        rel = vdf.relative_to(game_dir)
                        dest = snap / game_name / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(vdf, dest)
                        orig_hash = file_hash(vdf)
                        with open(dest.with_suffix(".meta.json"), "w", encoding="utf-8") as mf:
                            json.dump({
                                "original": str(vdf),
                                "game": game_name,
                                "timestamp": snap.name,
                                "hash": orig_hash,
                                "note": "full_archive",
                            }, mf, ensure_ascii=False)
                        backed += 1
                    except Exception as e:
                        print(f"存档失败 {vdf}: {e}")
        return backed, snap.name

    def manual_backup(self):
        self.status_var.set("正在存档全部 Steam 配置...")
        self.root.update()
        def do():
            try:
                n, ts = self.archive_all()
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("失败", f"存档失败: {e}"))
                self.root.after(0, lambda: self.status_var.set("存档失败"))
                return
            dest = Path(self.settings["backup_dir"]) / ts
            self.root.after(0, lambda: self.status_var.set(f"存档完成，共 {n} 个文件 → {dest}"))
            self.root.after(0, self.refresh_tree)
            self.root.after(0, lambda: messagebox.showinfo(
                "完成",
                f"已把当前 Steam 里的全部 VDF 存档，共 {n} 个文件。\n\n位置：\n{dest}\n\n不会删除本目录下已有文件。",
            ))
        threading.Thread(target=do, daemon=True).start()

    def _count_files(self, path: Path) -> int:
        if path.is_file():
            return 1
        n = 0
        for p in path.rglob("*"):
            if p.is_file() and p.suffix.lower() != ".json":
                n += 1
        return n

    def collect_old_archives(self):
        """找出可删除的旧存档，以及要保留的最近一次时间戳。"""
        backup_root = Path(self.settings["backup_dir"])
        if not backup_root.exists():
            return None, [], "还没有存档。"

        snaps = sorted(
            (p for p in backup_root.iterdir() if p.is_dir() and SNAPSHOT_DIR_RE.match(p.name)),
            key=lambda p: p.name,
            reverse=True,
        )
        loose_dirs = [p for p in backup_root.iterdir() if p.is_dir() and not SNAPSHOT_DIR_RE.match(p.name)]

        if snaps:
            keep = snaps[0]
            to_delete = snaps[1:] + loose_dirs
            keep_label = self._format_ts_dir(keep.name)
            return keep_label, to_delete, ""

        # 旧式按文件名带时间戳的备份：只保留全局最新的那一次时间戳
        stamped = []
        for game_dir in loose_dirs:
            for bak in game_dir.glob("*.vdf"):
                parts = bak.stem.rsplit("_", 2)
                if len(parts) < 3:
                    continue
                ts = f"{parts[-2]}_{parts[-1]}"
                try:
                    datetime.strptime(ts[:15], TIMESTAMP_FMT)
                except Exception:
                    continue
                stamped.append((ts, bak, game_dir))
        if not stamped:
            return None, [], "没有找到带时间戳的存档。"
        latest = max(s[0] for s in stamped)
        to_delete = []
        for ts, bak, game_dir in stamped:
            if ts != latest:
                to_delete.append(bak)
                meta = bak.with_suffix(".meta.json")
                if meta.exists():
                    to_delete.append(meta)
        if not to_delete:
            return self._format_ts_dir(latest), [], ""
        return self._format_ts_dir(latest), to_delete, ""

    def keep_latest_archive(self):
        keep_label, to_delete, err = self.collect_old_archives()
        if err:
            messagebox.showinfo("提示", err)
            return
        if not to_delete:
            messagebox.showinfo("提示", f"已经只有最近一次存档（{keep_label}），无需清理。")
            return
        file_count = sum(self._count_files(p) for p in to_delete)
        if not messagebox.askyesno(
            "确认清理",
            f"将只保留最近一次存档：{keep_label}\n\n"
            f"即将删除 {len(to_delete)} 项旧存档（约 {file_count} 个文件），删除后不能恢复。是否继续？",
        ):
            return
        failed = []
        removed = 0
        for path in to_delete:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
                removed += 1
            except Exception as e:
                failed.append(f"{path.name}: {e}")
        self.refresh_tree()
        if failed:
            messagebox.showwarning(
                "部分失败",
                f"已删除 {removed} 项，另有 {len(failed)} 项失败：\n" + "\n".join(failed[:8]),
            )
        else:
            messagebox.showinfo("完成", f"已只保留 {keep_label} 的存档，共清理 {removed} 项。")

    def _read_meta_original(self, bak: Path) -> str:
        meta_path = bak.with_suffix(".meta.json")
        if not meta_path.exists():
            return ""
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f).get("original", "") or ""
        except Exception:
            return ""

    def _size_str(self, path: Path) -> str:
        size = path.stat().st_size
        return f"{size/1024:.1f} KB" if size > 1024 else f"{size} B"

    def _format_ts_dir(self, name: str) -> str:
        try:
            dt = datetime.strptime(name[:15], TIMESTAMP_FMT)
            extra = name[15:]
            return dt.strftime("%Y-%m-%d %H:%M:%S") + extra
        except Exception:
            return name

    def _collect_vdfs(self, folder: Path):
        items = []
        for bak in folder.rglob("*.vdf"):
            if bak.is_file():
                items.append(bak)
        return items

    # ---------- 树形列表刷新 ----------
    def refresh_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

        self.appid_names = build_appid_name_map(self.settings.get("steam_path"))

        backup_root = Path(self.settings["backup_dir"])
        if not backup_root.exists():
            self.status_var.set("还没有存档。点击「立即存档」才会读取 Steam 并保存到软件同目录。")
            return

        # game -> {"snapshots": [(ts_sort, time_display, snap_game_dir, [files...])], "files": {stem: [versions]}}
        games = {}

        def bucket(game_name):
            if game_name not in games:
                games[game_name] = {"snapshots": [], "files": {}}
            return games[game_name]

        def parse_old_backup(bak: Path):
            name = bak.stem
            parts = name.rsplit("_", 2)
            if len(parts) >= 3:
                orig_stem = "_".join(parts[:-2])
                ts_str = f"{parts[-2]}_{parts[-1]}"
                try:
                    dt = datetime.strptime(ts_str, TIMESTAMP_FMT)
                    time_display = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    time_display = "未知"
                    orig_stem = name
            else:
                orig_stem = name
                time_display = "未知"
            return orig_stem, time_display

        if backup_root.exists():
            for child in backup_root.iterdir():
                if not child.is_dir():
                    continue
                if SNAPSHOT_DIR_RE.match(child.name):
                    time_display = self._format_ts_dir(child.name)
                    for game_dir in child.iterdir():
                        if not game_dir.is_dir():
                            continue
                        files = []
                        for bak in self._collect_vdfs(game_dir):
                            orig_path = self._read_meta_original(bak)
                            files.append((bak.name, time_display, self._size_str(bak), orig_path, str(bak)))
                        if files:
                            bucket(game_dir.name)["snapshots"].append(
                                (child.name, time_display, str(game_dir), files)
                            )
                else:
                    game_name = child.name
                    files = bucket(game_name)["files"]
                    for bak in child.glob("*.vdf"):
                        orig_stem, time_display = parse_old_backup(bak)
                        orig_path = self._read_meta_original(bak)
                        files.setdefault(orig_stem, []).append(
                            (time_display, self._size_str(bak), orig_path, str(bak))
                        )

        for game_name, data in sorted(
            games.items(),
            key=lambda kv: (self.game_label(kv[0]).casefold(), kv[0]),
        ):
            game_node = self.tree.insert(
                "",
                "end",
                text=self.game_label(game_name),
                open=False,
                tags=("game",),
                values=(game_name,),
            )
            for ts_sort, time_display, snap_dir, files in sorted(
                data["snapshots"], key=lambda x: x[0], reverse=True
            ):
                snap_node = self.tree.insert(
                    game_node,
                    "end",
                    text=f"存档 {time_display}",
                    values=(time_display, f"{len(files)} 个文件", snap_dir, snap_dir),
                    tags=("snapshot",),
                )
                for fname, time_display, size_str, orig_path, bak_path in sorted(files, key=lambda x: x[0]):
                    self.tree.insert(
                        snap_node,
                        "end",
                        text=fname,
                        values=(time_display, size_str, orig_path, bak_path),
                        tags=("version",),
                    )
            for orig_stem, versions in data["files"].items():
                versions.sort(key=lambda x: x[0], reverse=True)
                for time_display, size_str, orig_path, bak_path in versions:
                    self.tree.insert(
                        game_node,
                        "end",
                        text=orig_stem + ".vdf",
                        values=(time_display, size_str, orig_path, bak_path),
                        tags=("version",),
                    )

        self.status_var.set(f"已加载 {len(games)} 个游戏的存档")

    def get_selected_backup_path(self):
        sel = self.tree.selection()
        if not sel:
            return None
        item = sel[0]
        values = self.tree.item(item, "values")
        if len(values) >= 4 and values[3]:
            return Path(values[3])
        return None

    def collect_migrate_sources(self):
        """返回 (来源游戏文件夹名, [(文件名不含扩展名, 备份路径), ...])。"""
        sel = self.tree.selection()
        if not sel:
            return None
        item = sel[0]
        tags = set(self.tree.item(item, "tags") or [])
        values = self.tree.item(item, "values")

        if "version" in tags:
            parent = self.tree.parent(item)
            parent_tags = set(self.tree.item(parent, "tags") or [])
            if "snapshot" in parent_tags:
                game_item = self.tree.parent(parent)
            else:
                game_item = parent
            pvals = self.tree.item(game_item, "values")
            if not pvals:
                return None
            game_key = pvals[0]
            filename = self.tree.item(item, "text")
            stem = Path(filename).stem if filename else ""
            if not stem or len(values) < 4:
                return None
            return game_key, [(stem, Path(values[3]))]

        if "snapshot" in tags:
            parent = self.tree.parent(item)
            pvals = self.tree.item(parent, "values")
            if not pvals:
                return None
            game_key = pvals[0]
            snap_dir = Path(values[3]) if len(values) >= 4 and values[3] else None
            if not snap_dir or not snap_dir.exists():
                return None
            items = []
            for bak in self._collect_vdfs(snap_dir):
                items.append((bak.stem, bak))
            return game_key, items if items else None

        if "game" in tags:
            game_key = values[0] if values else ""
            if not game_key:
                return None
            for child in self.tree.get_children(item):
                ctags = set(self.tree.item(child, "tags") or [])
                cvals = self.tree.item(child, "values")
                if "snapshot" in ctags and len(cvals) >= 4 and cvals[3]:
                    snap_dir = Path(cvals[3])
                    items = [(bak.stem, bak) for bak in self._collect_vdfs(snap_dir)]
                    return (game_key, items) if items else None
            latest = {}
            for child in self.tree.get_children(item):
                cvals = self.tree.item(child, "values")
                ctext = self.tree.item(child, "text")
                if len(cvals) >= 4 and ctext:
                    stem = Path(ctext).stem
                    if stem not in latest:
                        latest[stem] = Path(cvals[3])
            if not latest:
                return None
            return game_key, list(latest.items())

        return None

    def migrate_selected(self):
        sources = self.collect_migrate_sources()
        if not sources:
            messagebox.showwarning("提示", "请先选择一个游戏，或该游戏下的某个 VDF 备份。")
            return

        source_game, items = sources
        self.open_migrate_dialog(source_game, items)

    def open_migrate_dialog(self, source_game, items):
        win = Toplevel(self.root)
        win.title("迁移 VDF 到其他游戏")
        win.geometry("640x520")
        win.transient(self.root)
        win.grab_set()

        file_names = "、".join(stem + ".vdf" for stem, _ in items)
        if len(file_names) > 120:
            file_names = file_names[:117] + "..."

        ttk.Label(win, text="来源游戏:").grid(row=0, column=0, sticky="w", padx=10, pady=(12, 4))
        ttk.Label(win, text=self.game_label(source_game)).grid(row=0, column=1, sticky="w", padx=5, pady=(12, 4))
        ttk.Label(win, text="将迁移:").grid(row=1, column=0, sticky="nw", padx=10, pady=4)
        ttk.Label(win, text=f"{len(items)} 个文件：{file_names}", wraplength=480).grid(
            row=1, column=1, sticky="w", padx=5, pady=4
        )

        ttk.Label(win, text="目标游戏:").grid(row=2, column=0, sticky="nw", padx=10, pady=8)

        right = ttk.Frame(win)
        right.grid(row=2, column=1, sticky="nsew", padx=5, pady=8)
        filter_var = StringVar()
        ttk.Entry(right, textvariable=filter_var).pack(fill="x")
        listbox = Listbox(right, height=14, exportselection=False, selectmode=SINGLE)
        listbox.pack(fill="both", expand=True, pady=(6, 0))

        games = sorted(
            (k for k in self.collect_known_games() if k != source_game),
            key=lambda k: (self.game_label(k).casefold(), k),
        )
        shown = list(games)

        def refresh_list(*_):
            q = filter_var.get().strip().casefold()
            listbox.delete(0, END)
            shown.clear()
            for key in games:
                label = self.game_label(key)
                if not q or q in label.casefold() or q in key.casefold():
                    shown.append(key)
                    listbox.insert(END, label)
            if shown:
                listbox.selection_set(0)

        filter_var.trace_add("write", refresh_list)
        refresh_list()

        write_live = BooleanVar(value=True)
        ttk.Checkbutton(
            win,
            text="同时写入 Steam 实时配置目录（会先备份目标游戏的同名 VDF 再覆盖）",
            variable=write_live,
        ).grid(row=3, column=1, sticky="w", padx=5, pady=6)

        hint = (
            "上方是搜索过滤，请务必在列表里点选目标游戏。改完后需完全退出并重新打开 Steam 才会看到新布局。"
        )
        ttk.Label(win, text=hint, foreground="#555").grid(row=4, column=1, sticky="w", padx=5)

        def resolve_target():
            typed = filter_var.get().strip()
            sel = listbox.curselection()
            # 搜索框只用于过滤列表。只要选中了某项，就用选中的游戏，绝不能把搜索关键字当成新目录名。
            if sel:
                return shown[sel[0]]
            if typed:
                for key in games:
                    if typed == key or typed == self.game_label(key):
                        return key
                # 未选中任何项时，才允许把输入当作新的 AppID / 文件夹名
                return typed
            return ""

        def do_migrate():
            target = resolve_target()
            if not target:
                messagebox.showwarning("提示", "请选择或输入目标游戏。", parent=win)
                return
            if target == source_game:
                messagebox.showwarning("提示", "目标游戏不能与来源游戏相同。", parent=win)
                return
            if target not in games:
                if not messagebox.askyesno(
                    "未找到该游戏",
                    f"列表里没有“{target}”。\n\n"
                    "若这只是搜索关键字，请先在下面列表中点选真正的目标游戏。\n"
                    "只有在你确实要新建这个目录时才继续。\n\n是否仍要用这个名字新建目录？",
                    parent=win,
                ):
                    return
            confirm = (
                f"将把以下配置迁移到：\n{self.game_label(target)}\n\n"
                f"{file_names}\n\n"
            )
            if write_live.get():
                confirm += "会写入 Steam 实时配置，并先备份目标游戏的同名文件。"
            else:
                confirm += "仅复制到本软件的备份目录，不写入 Steam。"
            if not messagebox.askyesno("确认迁移", confirm, parent=win):
                return
            try:
                live_n, bak_n = self.perform_migrate(
                    source_game, items, target, write_live=write_live.get()
                )
            except Exception as e:
                messagebox.showerror("失败", f"迁移失败: {e}", parent=win)
                return
            win.destroy()
            self.refresh_tree()
            msg = f"已迁移 {bak_n} 个备份到 {self.game_label(target)}"
            if write_live.get():
                msg += f"，并写入 Steam 配置 {live_n} 处。请完全退出 Steam 后重新打开，再进目标游戏查看布局。"
            else:
                msg += "。未写入 Steam 实时配置。"
            self.status_var.set(msg)
            messagebox.showinfo("完成", msg)

        btns = ttk.Frame(win)
        btns.grid(row=5, column=1, sticky="e", padx=5, pady=16)
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="开始迁移", command=do_migrate).pack(side="right", padx=4)

        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(2, weight=1)

    def perform_migrate(self, source_game, items, target_game, write_live=True):
        backup_root = Path(self.settings["backup_dir"])
        target_backup = backup_root / target_game
        target_backup.mkdir(parents=True, exist_ok=True)

        live_dirs = []
        if write_live:
            live_dirs = [root / target_game for root in self.get_config_roots()]
            if not live_dirs:
                raise RuntimeError("未找到 Steam 配置目录，请先在设置里确认 Steam 安装路径。")

        ts = datetime.now().strftime(TIMESTAMP_FMT)
        live_written = 0
        bak_written = 0

        for stem, bak_path in items:
            bak_path = Path(bak_path)
            if not bak_path.exists():
                raise FileNotFoundError(f"找不到备份文件: {bak_path}")

            raw = bak_path.read_text(encoding="utf-8", errors="ignore")
            ctype = detect_controller_type(raw)

            live_path = ""
            for idx, live_dir in enumerate(live_dirs):
                live_dir.mkdir(parents=True, exist_ok=True)
                dest_names = [f"{stem}.vdf"]
                # 同时写入该手柄类型的当前布局文件，Steam 才会把它当作正在使用的配置
                if ctype and not stem.startswith("controller_"):
                    dest_names.append(f"{ctype}.vdf")
                # 去重并保持顺序
                seen_names = []
                for name in dest_names:
                    if name not in seen_names:
                        seen_names.append(name)

                for name in seen_names:
                    dest = live_dir / name
                    if dest.exists():
                        snap_stem = Path(name).stem
                        snap = target_backup / f"{snap_stem}_{ts}_pre_migrate.vdf"
                        n = 1
                        while snap.exists():
                            snap = target_backup / f"{snap_stem}_{ts}_pre_migrate_{n}.vdf"
                            n += 1
                        shutil.copy2(dest, snap)
                        orig_hash = file_hash(dest)
                        with open(snap.with_suffix(".meta.json"), "w", encoding="utf-8") as mf:
                            json.dump({
                                "original": str(dest),
                                "game": target_game,
                                "timestamp": ts,
                                "hash": orig_hash,
                                "note": "migrate_overwrite_backup",
                            }, mf, ensure_ascii=False)
                    patched = retarget_controller_vdf(raw, source_game, target_game, dest)
                    dest.write_text(patched, encoding="utf-8")
                    live_written += 1
                    if idx == 0 and name == f"{stem}.vdf":
                        live_path = str(dest)

            new_bak = target_backup / f"{stem}_{ts}.vdf"
            n = 1
            while new_bak.exists():
                new_bak = target_backup / f"{stem}_{ts}_{n}.vdf"
                n += 1
            shutil.copy2(bak_path, new_bak)
            with open(new_bak.with_suffix(".meta.json"), "w", encoding="utf-8") as mf:
                json.dump({
                    "original": live_path,
                    "game": target_game,
                    "timestamp": ts,
                    "hash": file_hash(bak_path),
                    "migrated_from": source_game,
                }, mf, ensure_ascii=False)
            bak_written += 1

        return live_written, bak_written

    def resolve_live_game_dir(self, game_name: str, hint_orig: str = "", create: bool = True) -> Path:
        """定位该游戏在 Steam 里的原始配置目录。"""
        if hint_orig:
            hint = Path(hint_orig)
            for cand in (hint, hint.parent, hint.parent.parent):
                if cand.name == game_name and cand.is_dir():
                    return cand
        roots = self.get_config_roots()
        for root in roots:
            d = root / game_name
            if d.is_dir():
                return d
        if not roots:
            raise RuntimeError("未找到 Steam 配置目录，请先在设置里确认 Steam 安装路径。")
        dest = roots[0] / game_name
        if not create:
            raise FileNotFoundError(f"未找到「{self.game_label(game_name)}」在 Steam 里的原 VDF 目录。")
        dest.mkdir(parents=True, exist_ok=True)
        return dest

    def get_selected_game_key(self):
        """返回 (游戏文件夹名, 原始路径提示)。"""
        sel = self.tree.selection()
        if not sel:
            return None, ""
        item = sel[0]
        tags = set(self.tree.item(item, "tags") or [])
        values = self.tree.item(item, "values")
        hint = values[2] if len(values) >= 3 else ""

        def game_from(node):
            vals = self.tree.item(node, "values")
            return vals[0] if vals else None

        if "game" in tags:
            return game_from(item), hint
        if "snapshot" in tags:
            return game_from(self.tree.parent(item)), hint
        if "version" in tags:
            parent = self.tree.parent(item)
            parent_tags = set(self.tree.item(parent, "tags") or [])
            if "snapshot" in parent_tags:
                parent = self.tree.parent(parent)
            return game_from(parent), hint
        return None, hint

    def collect_restore_job(self):
        """根据当前选中项，决定要恢复的游戏以及源文件列表 [(目标文件名, 源路径, 原始路径提示), ...]。"""
        sel = self.tree.selection()
        if not sel:
            return None
        item = sel[0]
        tags = set(self.tree.item(item, "tags") or [])
        values = self.tree.item(item, "values")

        def files_from_snapshot_dir(snap_dir: Path, game_key: str):
            jobs = []
            for bak in self._collect_vdfs(snap_dir):
                rel = bak.relative_to(snap_dir)
                jobs.append((str(rel), bak, self._read_meta_original(bak)))
            return game_key, jobs

        def latest_jobs_for_game(game_item, game_key):
            # 优先用最新一份「存档 时间」快照；否则每个旧式备份取最新一份
            snapshots = []
            loose = {}
            for child in self.tree.get_children(game_item):
                ctags = set(self.tree.item(child, "tags") or [])
                cvals = self.tree.item(child, "values")
                if "snapshot" in ctags and len(cvals) >= 4 and cvals[3]:
                    snapshots.append((self.tree.item(child, "text"), Path(cvals[3])))
                elif "version" in ctags and len(cvals) >= 4 and cvals[3]:
                    stem = Path(self.tree.item(child, "text")).stem
                    # 子节点已按时间新到旧插入，第一次见到的就是最新
                    if stem not in loose:
                        loose[stem] = (f"{stem}.vdf", Path(cvals[3]), cvals[2] if len(cvals) >= 3 else "")
            if snapshots:
                # 列表里快照已按新到旧排
                return files_from_snapshot_dir(snapshots[0][1], game_key)
            jobs = list(loose.values())
            return (game_key, jobs) if jobs else None

        if "game" in tags:
            game_key = values[0] if values else ""
            if not game_key:
                return None
            return latest_jobs_for_game(item, game_key)

        if "snapshot" in tags:
            parent = self.tree.parent(item)
            pvals = self.tree.item(parent, "values")
            game_key = pvals[0] if pvals else ""
            snap_dir = Path(values[3]) if len(values) >= 4 and values[3] else None
            if not game_key or not snap_dir:
                return None
            return files_from_snapshot_dir(snap_dir, game_key)

        if "version" in tags:
            parent = self.tree.parent(item)
            parent_tags = set(self.tree.item(parent, "tags") or [])
            if "snapshot" in parent_tags:
                # 选中快照里的单个文件：只恢复这一份
                game_item = self.tree.parent(parent)
                gvals = self.tree.item(game_item, "values")
                game_key = gvals[0] if gvals else ""
                bak = Path(values[3]) if len(values) >= 4 and values[3] else None
                if not game_key or not bak:
                    return None
                snap_dir = Path(self.tree.item(parent, "values")[3])
                try:
                    rel = bak.relative_to(snap_dir)
                except Exception:
                    rel = Path(bak.name)
                return game_key, [(str(rel), bak, values[2] if len(values) >= 3 else "")]
            else:
                game_key = self.tree.item(parent, "values")[0]
                bak = Path(values[3]) if len(values) >= 4 and values[3] else None
                if not bak:
                    return None
                name = self.tree.item(item, "text") or (bak.stem + ".vdf")
                return game_key, [(name, bak, values[2] if len(values) >= 3 else "")]

        return None

    def restore_selected(self):
        job = self.collect_restore_job()
        if not job:
            messagebox.showwarning("提示", "请先选择一个游戏（或其中一份存档），再恢复到该游戏原来的 Steam 目录。")
            return
        game_key, jobs = job
        if not jobs:
            messagebox.showwarning("提示", "这个游戏还没有可恢复的存档文件。")
            return

        hint = next((h for _, _, h in jobs if h), "")
        try:
            dest_dir = self.resolve_live_game_dir(game_key, hint)
        except Exception as e:
            messagebox.showerror("失败", str(e))
            return

        names = "、".join(Path(n).name for n, _, _ in jobs[:8])
        if len(jobs) > 8:
            names += f" 等共 {len(jobs)} 个文件"
        if not messagebox.askyesno(
            "确认恢复",
            f"将把「{self.game_label(game_key)}」的存档复制到原来的游戏目录：\n{dest_dir}\n\n{names}\n\n目标位置的同名文件会被覆盖，是否继续？",
        ):
            return
        try:
            written = 0
            for rel, src, _hint in jobs:
                if not src.exists():
                    raise FileNotFoundError(f"找不到存档文件: {src}")
                dest = dest_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                written += 1
            messagebox.showinfo("成功", f"已恢复 {written} 个文件到：\n{dest_dir}\n\n建议完全退出后再打开 Steam，使布局生效。")
            self.status_var.set(f"已恢复 {written} 个文件 → {dest_dir}")
        except Exception as e:
            messagebox.showerror("失败", f"恢复失败: {e}")

    def open_live_game_folder(self):
        game_key, hint = self.get_selected_game_key()
        if not game_key:
            messagebox.showwarning("提示", "请先选择一个游戏，再打开它在 Steam 里的原 VDF 文件夹。")
            return
        try:
            dest = self.resolve_live_game_dir(game_key, hint, create=False)
        except Exception as e:
            messagebox.showerror("失败", str(e))
            return
        os.startfile(str(dest))
        self.status_var.set(f"已打开原目录: {dest}")

    def open_backup_folder(self):
        bak = self.get_selected_backup_path()
        if bak:
            os.startfile(str(bak if bak.is_dir() else bak.parent))
        else:
            os.startfile(self.settings["backup_dir"])

    # ---------- 设置窗口 ----------
    def open_settings(self):
        win = ttk.Toplevel(self.root)
        win.title("设置")
        win.geometry("680x360")
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="Steam 安装路径:").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        steam_var = StringVar(value=self.settings.get("steam_path", ""))
        ttk.Entry(win, textvariable=steam_var, width=50).grid(row=0, column=1, padx=5)
        ttk.Button(win, text="浏览", command=lambda: steam_var.set(filedialog.askdirectory())).grid(row=0, column=2)
        ttk.Button(win, text="自动检测", command=lambda: steam_var.set(str(detect_steam_path() or ""))).grid(row=0, column=3)

        ttk.Label(win, text="存档目录:").grid(row=1, column=0, sticky="w", padx=10, pady=8)
        ttk.Label(win, text=str(DEFAULT_BACKUP_DIR), wraplength=420).grid(row=1, column=1, sticky="w", padx=5)
        ttk.Label(
            win,
            text="固定在软件同目录下。启动时只读取这里的存档；只有点击「立即存档」才会读取 Steam 里的 VDF。已有文件不会删除。",
            foreground="#555",
            wraplength=420,
        ).grid(row=2, column=1, sticky="w", padx=5, pady=(0, 8))

        common_var = BooleanVar(value=self.settings.get("monitor_common", True))
        ttk.Checkbutton(win, text="立即存档时包含 Steam Controller Configs 目录", variable=common_var).grid(row=3, column=1, sticky="w")

        userdata_var = BooleanVar(value=self.settings.get("monitor_userdata", True))
        ttk.Checkbutton(win, text="立即存档时包含 userdata/241100/remote/controller_config 目录", variable=userdata_var).grid(row=4, column=1, sticky="w")

        def save():
            self.settings["steam_path"] = steam_var.get()
            self.settings["backup_dir"] = str(DEFAULT_BACKUP_DIR)
            self.settings["auto_backup"] = False
            self.settings["monitor_common"] = common_var.get()
            self.settings["monitor_userdata"] = userdata_var.get()
            self.save_settings()
            win.destroy()
            self.refresh_tree()
            messagebox.showinfo("成功", "设置已保存")

        ttk.Button(win, text="保存", command=save).grid(row=6, column=1, pady=20)

    # ---------- 系统托盘 ----------
    def create_tray_icon(self):
        # 支持开发环境和 PyInstaller 打包后的环境
        if getattr(sys, 'frozen', False):
            base_path = Path(sys._MEIPASS)
        else:
            base_path = Path(__file__).parent

        icon_path = base_path / "icon.png"
        if icon_path.exists():
            image = Image.open(icon_path).resize((64, 64), Image.LANCZOS)
        else:
            # 绘制一个简单图标作为后备
            image = Image.new("RGB", (64, 64), color=(30, 144, 255))
            draw = ImageDraw.Draw(image)
            draw.ellipse((8, 8, 56, 56), fill="white")
            draw.rectangle((26, 18, 38, 46), fill=(30, 144, 255))
            draw.polygon([(32, 10), (42, 24), (22, 24)], fill=(30, 144, 255))

        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", self.show_from_tray),
            pystray.MenuItem("立即存档", lambda: self.root.after(0, self.manual_backup)),
            pystray.MenuItem("退出", self.quit_app)
        )
        self.tray_icon = pystray.Icon(APP_NAME, image, "Steam Input 备份管理器", menu)

    def hide_to_tray(self):
        if not self.tray_icon:
            self.create_tray_icon()
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        self.root.withdraw()
        self.status_var.set("已隐藏到系统托盘")

    def show_from_tray(self, icon=None, item=None):
        def _show():
            self.root.deiconify()
            self.root.lift()
            try:
                self.root.attributes("-topmost", True)
                self.root.after(200, lambda: self.root.attributes("-topmost", False))
            except Exception:
                pass
            self.root.focus_force()
        self.root.after(0, _show)

    def on_close(self):
        # 关闭按钮改为隐藏到托盘
        self.hide_to_tray()

    def quit_app(self, icon=None, item=None):
        self.running = False
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.root.destroy)
        sys.exit(0)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    # Windows 下高 DPI 支持（可选）
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    handles = acquire_single_instance()
    if not handles:
        sys.exit(0)

    mutex, event = handles
    try:
        app = SteamInputBackupApp()
        start_activate_watcher(app, event)
        app.run()
    finally:
        if event:
            _KERNEL32.CloseHandle(event)
        if mutex:
            _KERNEL32.CloseHandle(mutex)

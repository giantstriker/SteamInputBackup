# Steam Input Backup

Windows 下备份、恢复和迁移 Steam Input（手柄布局）VDF 配置的图形界面工具。

A Windows desktop tool to back up, restore, and migrate Steam Input controller layout VDF files.

## 简介

Steam Input Backup 是一个 Windows 工具，用来备份、恢复和迁移 Steam 手柄布局（VDF）。打开软件时只读取软件同目录下的 `存档`，不会自动扫描 Steam。


## 功能

- **立即存档**：读取 Steam 里当前全部本地 VDF，按统一时间戳保存到软件同目录下的 `存档` 文件夹
- **启动只读本地存档**：打开软件时只读取同目录 `存档`，不会扫描 Steam
- **恢复到 Steam**：选择一个游戏（或其中一份存档），把文件复制回该游戏原来的配置目录
- **迁移 VDF**：把一份布局写入 Steam 里的另一个游戏，并改写 `usercloud` / `autosave` 路径；不会在软件存档目录再复制一份，需要时请自行「立即存档」
- **定时对比存档**：可在设置里选择关闭、1 分钟、10 分钟、1 小时或 1 天；到期后对比最新时间戳存档与 Steam 原文件的 MD5，不同则自动存档
- **备份次数上限**：在设置里指定 10–100 次（默认 20）。新存档后若超过上限，自动删除最旧的时间戳存档
- **只保留最近存档**：一键清理，只留下时间戳最新的一份（会先确认）
- **打开原 VDF 目录**：打开所选游戏在 Steam 里的原始配置文件夹
- **打开备份所在文件夹**：仅在选中 VDF 时，用资源管理器定位该文件，不会打开整个存档目录
- 可选功能：在设置里打开定时对比（1 分钟 / 10 分钟 / 1 小时 / 1 天），若最新存档与 Steam 原文件 MD5 不同会自动再存一份；也可设置备份次数上限（10–100，默认 20）。恢复或迁移后，建议完全退出再打开 Steam，布局才会稳定生效。
- 列表优先显示 Steam 前端游戏名，AppID 放在后面
- 单实例运行：重复打开会切回已有窗口
- 可隐藏到系统托盘
- 只备份本机 `Steam Controller Configs` 目录（不含 Steam 云同步目录）



## 运行环境

- Windows
- Python 3.8+（推荐 3.9+）
- 依赖见 `requirements.txt`（`pystray`、`Pillow`）；界面使用 Python 自带的 tkinter

## 存档位置与设置

- 时间戳存档固定在软件同目录（脚本或 exe 所在目录）下的 `存档\YYYYMMDD_HHMMSS\`
- 程序自身设置保存在 `%APPDATA%\SteamInputBackup\config.json`
- Steam 安装路径可在设置中手动指定或自动检测

## 文件说明

- `steam_input_backup.py`：主程序
- `requirements.txt`：依赖（pystray、Pillow）
- `icon.png`：窗口 / 托盘图标

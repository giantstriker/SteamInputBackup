# Steam Input 配置备份管理器

用于备份、恢复和迁移 Steam Input（手柄布局）VDF 配置的 Windows 图形界面工具。

## 功能

- **立即存档**：读取 Steam 里当前全部 VDF，按统一时间戳保存到软件同目录下的 `存档` 文件夹，不会自动删除已有文件
- **启动只读本地存档**：打开软件时只读取同目录 `存档`，不会扫描 Steam
- **恢复到 Steam**：选择一个游戏（或其中一份存档），把文件复制回该游戏原来的配置目录
- **迁移 VDF**：把一份布局迁移到另一个游戏，并改写 `usercloud` / `autosave` 路径，使 Steam 能识别为新游戏的配置
- **只保留最近存档**：一键清理，只留下时间戳最新的一份（会先确认）
- **打开原 VDF 目录**：打开所选游戏在 Steam 里的原始配置文件夹
- 列表优先显示 Steam 前端游戏名，AppID 放在后面
- 单实例运行：重复打开会切回已有窗口
- 可隐藏到系统托盘

## 运行

需要 Python 3.8+（推荐 3.9+）和 Windows。

```bash
pip install -r requirements.txt
python steam_input_backup.py
```

## 打包为 EXE

双击 `build_exe.bat`，或在项目目录执行：

```bash
pip install pyinstaller pystray Pillow
python -m PyInstaller --noconfirm --onefile --windowed --name SteamInputBackup --icon=icon.png --add-data "icon.png;." --hidden-import=pystray --hidden-import=PIL steam_input_backup.py
```

生成的文件在 `dist\SteamInputBackup.exe`。运行后，存档目录是 exe 同目录下的 `存档`。

## 注意事项

- 改完 Steam 配置后，建议完全退出再打开 Steam，布局才会稳定生效
- 程序自身设置保存在 `%APPDATA%\SteamInputBackup\config.json`
- 请不要把 `存档` 目录或个人 VDF 备份提交到 Git 仓库

## 文件说明

- `steam_input_backup.py`：主程序
- `requirements.txt`：依赖（pystray、Pillow）
- `build_exe.bat`：一键编译脚本
- `icon.png`：窗口 / 托盘图标

"""把项目打成发给朋友试用的内测包。

包里有什么
----------
运行游戏界面真正需要的东西：`main.py`、`src/`（前端 + 服务端 + 两个运行库）、
`data/wowgear.db`（4.7MB，游戏数据）、`data/icons/`（1.9MB，离线图标）、
`data/stat_curve.csv`（属性曲线）、一个 `启动.bat` 和一份说明。

**故意不带**的东西，以及为什么：
- `data/accounts.db` —— 你自己的账号、历史、配装存档
- `data/panel.json` / `player_crafted.json` —— 你自己角色的面板校准和自选属性，
  别人导入自己的角色时会被这两份带偏
- `data/raw/`（57MB）—— 只有 `--build` 重建库才用，运行时不需要
- `src/build/`、`tools/`、`ref/`、`.workbuddy/`、`.idea/` —— 开发期的东西
- `tests/my_profile.txt` —— 你的真实角色导出（含角色名和服务器）
  只保留构造出来的 `sample_profile.txt`，落地页「用样例」还能用

用法
----
    python tools/package_beta.py          # 生成 dist/魔兽装备规划器-内测版-v<版本>.zip
    python tools/package_beta.py --keep   # 保留解压后的目录，方便先自己点一遍
"""

import argparse
import io
import os
import shutil
import sys
import zipfile
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
NAME = "魔兽装备规划器-内测版"

# 目录里要递归带上的（相对 ROOT）
DIRS = [
    "src/wowgear", "src/wowlib", "src/server", "src/web",
]
# 单个文件
FILES = [
    "main.py", "VERSION", "data/wowgear.db", "data/stat_curve.csv",
    "tests/sample_profile.txt",
]
# 递归时跳过的文件名/目录名
SKIP_NAMES = {"__pycache__", "_shot.html", ".DS_Store"}
SKIP_SUFFIX = (".pyc", ".pyo")

BAT = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 魔兽装备规划器（内测版）

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   没找到 Python。先装一个 3.10 或更新的版本：
  echo   https://www.python.org/downloads/
  echo   安装时记得勾选 "Add python.exe to PATH"。
  echo.
  pause
  exit /b 1
)

echo.
echo   正在启动，浏览器会自动打开 http://127.0.0.1:8765
echo   关掉这个黑窗口就停止服务。
echo.
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8765"
python main.py --web
pause
"""

README = """魔兽装备规划器 · 内测版 {version}
================================================

这是什么
--------
一个离线的魔兽装备规划器：把游戏里 /simc 导出的文本贴进来，就能在这儿试
"换哪件装备、插哪颗宝石、附什么魔，属性会变成什么样"，满意了再导出回
simc 文本丢给 Raidbots 跑模拟。

它算的是**属性**，不是 DPS。Raidbots 能告诉你现在这一身打多少，但它算不出
"换成那件之后我的暴击会少多少"——这个工具补的就是这一块。


怎么用
------
1. 双击「启动.bat」。会弹一个黑窗口，浏览器自动打开。
   （关掉黑窗口 = 停止服务；下次用再双击一次即可。）
2. 入口页点「以游客身份浏览」就能用全部模拟功能。
   想存配装、留 simc 历史，就注册一个账号（只存在你自己电脑上）。
3. 进主界面后点右上角「导入 simc」，把游戏里 /simc 弹出的一整段粘进去。
4. 左边是装备栏，点部位 → 右边出候选；鼠标划过卡片就能看到"换上后属性变成多少"。
5. 右上角「导出 simc」把当前这套导出去，粘进 Raidbots 的 Quick Sim。

想核对面板：点顶栏「面板校准」，把游戏里悬停属性看到的**评级**填一遍
（敏捷/耐力/暴击/急速/精通/全能），之后顶部显示的就是你的真实面板值。


已知边界（不是 bug，先说一声）
----------------------------
- **只算装备本体 + 宝石 + 附魔**。天赋、等级自带的基础属性不在里面，
  所以不填面板校准时，数字会比游戏里低一截。
- **套装效果不算属性**。大多数套装是伤害 proc，本来就不改面板。
- **打孔器开的孔导不到 Raidbots**。SimC 格式里没有"额外插槽"这个概念，
  导出时会弹提示点名哪几件。
- **腿部附魔的数值是推算的**（护甲片/魔线，没有公开 tooltip 可抓）。
  其余 38 条附魔是从游戏 tooltip 原文解的。
- 数据是 12.1.5.70575 这个 build 的（Midnight Season 2），换赛季要重新建库，
  内测版没带建库用的原始数据。


需要什么环境
------------
Windows + Python 3.10 或更新。不需要联网，不需要装任何第三方库。


数据存在哪
----------
全在压缩包解出来的这个文件夹里：
  data/accounts.db      账号、历史、配装存档（删了就没了）
  data/panel.json       你填的面板校准
  data/player_crafted.json  你确认过的制造业/装绑属性
想搬去别的电脑，把整个文件夹拷过去就行。


反馈什么最有用
--------------
- 数字和游戏里对不上：最好给一张物品/角色面板的截图，并说明是在哪个界面看到的
- 界面哪里别扭、点不出来、看不懂
- 缺什么功能

版本 {version} · 打包时间 {date}
"""


def _copy_tree(src, dst):
    """递归复制，跳过 __pycache__ 和开发用文件。"""
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_NAMES]
        rel = os.path.relpath(base, src)
        target = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(target, exist_ok=True)
        for f in files:
            if f in SKIP_NAMES or f.endswith(SKIP_SUFFIX):
                continue
            shutil.copy2(os.path.join(base, f), os.path.join(target, f))


def build(keep=False):
    version = "0.0.0"
    vpath = os.path.join(ROOT, "VERSION")
    if os.path.exists(vpath):
        with io.open(vpath, encoding="utf-8") as f:
            version = f.read().strip() or version
    tag = f"{NAME}-v{version.split('-')[0]}"

    if os.path.exists(DIST):
        shutil.rmtree(DIST)
    stage = os.path.join(DIST, tag)
    os.makedirs(stage, exist_ok=True)

    for d in DIRS:
        _copy_tree(os.path.join(ROOT, d), os.path.join(stage, d))
    for f in FILES:
        src = os.path.join(ROOT, f)
        if not os.path.exists(src):
            print(f"  ⚠ 缺 {f}，跳过")
            continue
        dst = os.path.join(stage, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    # 图标单独走一遍：目录很大，用同一套跳过规则
    _copy_tree(os.path.join(ROOT, "data", "icons"),
               os.path.join(stage, "data", "icons"))

    date = datetime.now().strftime("%Y-%m-%d")
    with io.open(os.path.join(stage, "使用说明.txt"), "w",
                 encoding="utf-8-sig", newline="\r\n") as f:
        f.write(README.format(version=version, date=date))
    with io.open(os.path.join(stage, "启动.bat"), "w",
                 encoding="utf-8", newline="\r\n") as f:
        f.write(BAT)

    # 压缩包：Windows 上别人解压后中文名不乱码，用 UTF-8 文件名标志
    zpath = os.path.join(DIST, tag + ".zip")
    count = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for base, _dirs, files in os.walk(stage):
            for f in files:
                full = os.path.join(base, f)
                arc = os.path.join(tag, os.path.relpath(full, stage))
                z.write(full, arc)
                count += 1

    size = os.path.getsize(zpath) / 1048576
    print(f"打包完成：{os.path.relpath(zpath, ROOT)}")
    print(f"  {count} 个文件，{size:.1f} MB")
    print(f"  解压后目录：{os.path.relpath(stage, ROOT)}")
    if not keep:
        shutil.rmtree(stage)
        print("  （临时目录已删；加 --keep 可保留，方便自己先点一遍）")
    return zpath


def main(argv=None):
    ap = argparse.ArgumentParser(description="打内测包")
    ap.add_argument("--keep", action="store_true", help="保留解压后的目录")
    args = ap.parse_args(argv)
    build(args.keep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

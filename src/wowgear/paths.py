"""所有路径的唯一来源。

其它模块一律 `from .paths import DB_PATH`，不再自己拼 `../data/...`——
路径散落在十几个文件里，哪天挪个目录就得全改，而且改漏了要到运行时报错才发现。
"""

import os

# src/wowgear/paths.py -> src/wowgear -> src -> 项目根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw")
ICONS = os.path.join(DATA, "icons")
WEB = os.path.join(ROOT, "src", "web")
TESTS = os.path.join(ROOT, "tests")

DB_PATH = os.path.join(DATA, "wowgear.db")
STAT_CURVE = os.path.join(DATA, "stat_curve.csv")
PANEL_PATH = os.path.join(DATA, "panel.json")
CHOICE_PATH = os.path.join(DATA, "player_crafted.json")
RPP_PATH = os.path.join(RAW, "rand-prop-points.csv")
# 账号库必须独立于 wowgear.db：GameData 随赛季整库重建，账号重建一次就全没了
ACCOUNT_DB = os.path.join(DATA, "accounts.db")


def ensure_dirs():
    for p in (DATA, RAW, ICONS):
        os.makedirs(p, exist_ok=True)

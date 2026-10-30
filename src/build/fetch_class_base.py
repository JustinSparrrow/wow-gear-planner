"""抽「裸装基础属性」——每个职业、每个等级的初始面板值。

为什么需要
----------
面板校准是"玩家自己填"，换角色、换专精都得重填一遍。其实**职业+等级**的那部分
是游戏数据能算出来的：客户端 `gtClassStatsByLevel` 表给出每个职业每一级的
力量/敏捷/耐力/智力，`gtRaceStats` 再叠一点种族差异（血精灵 +2 智力那种）。

有了它，没填过面板的人也能看到"职业基础 + 装备"的绝对值，而不是只有装备本体。

⚠️ 它**不含天赋和专精被动**。真实角色裸装的敏捷会比这个数高（天赋会给百分比加成），
所以界面上标的是"职业基础 · 未含天赋"。填过面板的仍然以面板为准。

数据从哪来
----------
`gt*` 这几张表 wago.tools 不提供 CSV，能拿到的是 SimC 生成的
`engine/dbc/sc_extra_data.inc`（dbc_extract 从客户端导出的）。这个脚本
要么读本地 SimC 源码（`--from-file`），要么自己下 SimC 仓库的 zip 解出来。

用法
----
    python src/build/fetch_class_base.py                    # 自动下载 SimC 源码（需代理）
    python src/build/fetch_class_base.py --proxy http://127.0.0.1:7897
    python src/build/fetch_class_base.py --from-file D:\\simc\\engine\\dbc\\sc_extra_data.inc

产物 `data/raw/class_base.json`：

    {"classes": {"hunter": {"90": {"str":378,"agi":620,"sta":4600,"int":509}}},
     "races":   {"blood_elf": {"str":-3,"agi":1,"sta":0,"int":2}},
     "meta":    {...}}
"""

import argparse
import io
import json
import os
import re
import sys
import tempfile
import urllib.request
import zipfile

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(RAW, "class_base.json")

SIMC_ZIP = "https://codeload.github.com/simulationcraft/simc/zip/refs/heads/midnight"
INC_PATH = "engine/dbc/sc_extra_data.inc"

# 种族名（注释里是英文）-> simc profile 里的 race 键
RACE_KEYS = {
    "human": "human", "orc": "orc", "dwarf": "dwarf", "night elf": "night_elf",
    "undead": "undead", "tauren": "tauren", "gnome": "gnome", "troll": "troll",
    "goblin": "goblin", "blood elf": "blood_elf", "draenei": "draenei",
    "worgen": "worgen", "pandaren": "pandaren", "nightborne": "nightborne",
    "void elf": "void_elf", "lightforged draenei": "lightforged_draenei",
    "highmountain tauren": "highmountain_tauren", "mag'har orc": "maghar_orc",
    "dark iron dwarf": "dark_iron_dwarf", "zandalari troll": "zandalari_troll",
    "kul tiran": "kul_tiran", "vulpera": "vulpera", "mechagnome": "mechagnome",
    "dracthyr": "dracthyr", "earthen": "earthen", "haranir": "haranir",
}


def _rows(text):
    """从一段 C++ 初始化列表里按顺序取 {a,b,c,d,e} 行。"""
    out = []
    for m in re.finditer(r"\{\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\}", text):
        out.append([int(x) for x in m.groups()])
    return out


# 表按 class_id 排：0 = 无职业，1..13 就是下面这个顺序（SimC 的 class_id 定义）。
# 不靠注释认名字——注释里会带 "TODO: 81-89" 这种尾巴，正则容易认错。
CLASS_ORDER = ["warrior", "paladin", "hunter", "rogue", "priest", "deathknight",
               "shaman", "mage", "warlock", "monk", "druid", "demonhunter", "evoker"]


def parse(text):
    start = text.index("__gt_class_stats_by_level")
    end = text.index("__gt_race_stats")
    rows = _rows(text[start:end])
    per_class = 120
    n_classes = len(rows) // per_class
    classes = {}
    for idx in range(1, n_classes):          # 第 0 组是 "No Class"
        key = CLASS_ORDER[idx - 1] if idx - 1 < len(CLASS_ORDER) else f"class{idx}"
        block = rows[idx * per_class:(idx + 1) * per_class]
        classes[key] = {
            str(lv): dict(zip(("str", "agi", "sta", "int", "spi"), block[lv - 1]))
            for lv in range(1, len(block) + 1)
        }

    # 种族表长这样：`{  -3,  1,  0,  2,  0 }, // Blood Elf` —— 注释在行尾
    races = {}
    race_block = text[end:text.index("};", end)]
    for line in race_block.splitlines():
        m = re.search(r"\{\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\}"
                      r"\s*,?\s*//\s*([A-Za-z' \-]+)", line)
        if not m:
            continue
        vals = [int(m.group(i)) for i in range(1, 6)]
        name = m.group(6).strip().lower().split(" - ")[0]
        key = RACE_KEYS.get(name)
        if not key or key in races or not any(vals):
            continue
        races[key] = dict(zip(("str", "agi", "sta", "int", "spi"), vals))
    return classes, races


def _read_from_zip(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.endswith(INC_PATH))
        return z.read(name).decode("utf-8")


def download_simc(proxy=""):
    """下 SimC 源码 zip，只取那一个 inc 文件。约 50MB，解压前就能读。"""
    print(f"下载 {SIMC_ZIP}")
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    tmp = os.path.join(tempfile.mkdtemp(), "simc.zip")
    with opener.open(urllib.request.Request(
            SIMC_ZIP, headers={"User-Agent": "Mozilla/5.0"}), timeout=600) as r, \
            io.open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    print(f"  {os.path.getsize(tmp) / 1048576:.0f} MB")
    try:
        return _read_from_zip(tmp)
    finally:
        os.remove(tmp)


def main(argv=None):
    ap = argparse.ArgumentParser(description="抽裸装基础属性")
    ap.add_argument("--from-file", help="本地 SimC 的 sc_extra_data.inc 路径")
    ap.add_argument("--proxy", default=os.environ.get("WG_PROXY", ""),
                    help="下载用代理，例如 http://127.0.0.1:7897")
    args = ap.parse_args(argv)

    if args.from_file:
        with io.open(args.from_file, encoding="utf-8") as f:
            text = f.read()
        source = args.from_file
    else:
        text = download_simc(args.proxy)
        source = f"{SIMC_ZIP} :: {INC_PATH}"

    classes, races = parse(text)
    if not classes:
        print("没解析出任何职业，检查 SimC 源码版本")
        return 1

    build = ""
    meta_path = os.path.join(RAW, "meta.json")
    if os.path.exists(meta_path):
        with io.open(meta_path, encoding="utf-8") as f:
            build = json.load(f).get("wowBuild") or ""

    out = {
        "_meta": {
            "source": source,
            "wow_build": build,
            "note": ("裸装基础属性（职业 + 种族），不含天赋和专精被动。"
                     "等级取 1-120，键是等级字符串。"),
            # ⚠️ 这批 gt 表是**压缩前**的标尺：SimC 的 hp_per_sta(90)=75，
            # 而 12.1.5 游戏面板里 生命值/耐力 = 22。用真实 90 级猎人的面板
            # （敏捷 2626 / 耐力 42210，2026-10-31）反推，职业基础要 ×1.5
            # 才和游戏对得上：620×1.5+种族=931，4600×1.5=6900。
            # 换资料片或压缩属性之后这个数要重新拟合。
            "scale": 1.5,
        },
        "classes": classes,
        "races": races,
    }
    os.makedirs(RAW, exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)

    print(f"职业 {len(classes)} 个、种族 {len(races)} 个 -> {os.path.relpath(OUT, ROOT)}")
    for key in ("hunter", "warrior", "priest"):
        if key in classes and "90" in classes[key]:
            v = classes[key]["90"]
            print(f"  {key:10} 90 级：力量 {v['str']} 敏捷 {v['agi']} 耐力 {v['sta']} 智力 {v['int']}")
    print("\n下一步：python main.py --build  写进数据库")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

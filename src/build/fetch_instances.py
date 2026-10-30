"""抓副本和首领的中英对照名。

为什么需要
----------
装备名有中文（`names_zhcn.json` 从客户端 ItemSparse 抽的），但**掉落来源
一直是英文**："Temple of Sethaliss · Galvazzt"。这是从 Blizzard API 的
`item_sources` 表来的，那边只给英文。

后果在搜索上最明显：玩家想找"阿曼尼"那个副本出的装备，输入中文——一件
也搜不到，因为整张表里没有一个中文字。玩家对副本的中文名有印象（界面、
攻略、集合石都是中文），对装备英文名没有。

── 为什么不按 id 直接查 ──────────────────────────────────
第一版是 `instance_id -> 中文名`，结果撞出一个错配：

    encounter_name "Ruby Life Pools"（红玉新生法池） -> 奥尔高格

奥尔高格是黑石铸造厂的首领，跟红玉新生法池毫无关系。原因是
`item_sources.encounter_id` 并不总是 `JournalEncounter.ID`——当数据源
不知道具体首领时，它会把副本本身当成 "encounter" 填进来，id 自然对不上。

所以改成**按名字对齐**：同一条记录分别取 enUS 和 zhCN 两份表的 `Name_lang`，
按 ID 配对成 `{"English": "中文"}`。之后拿英文名去查表，查不到就保持原样。
名字是玩家实际看到的东西，对不上时宁可保留英文，也不能张冠李戴。

产物
----
data/raw/instances_zhcn.json

    {"pairs": {"Kings' Rest": "诸王之眠",
               "The Council of Tribes": "部族议会", ...},
     "byInstanceId": {"1041": "诸王之眠"},
     "byEncounterId": {"2635": "部族议会"}}

三份都留着：pairs 是主用（按名字，不会错配），按 id 的两份留着给别的
地方可能需要。

用法
----
    python src/build/fetch_instances.py
    python src/build/fetch_instances.py --proxy http://127.0.0.1:7897
"""

import argparse
import csv
import io
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
META = os.path.join(ROOT, "data", "raw", "meta.json")
OUT = os.path.join(ROOT, "data", "raw", "instances_zhcn.json")

URL = "https://wago.tools/db2/{table}/csv?build={build}&locale={locale}"

#
# 手工补的两类名字。
#
# 一类是专业名（掉落来源写的是 "Tailoring"），客户端没有对应的
# Journal 条目；另一类是 `item_sources` 自己造的分类名
# （"Mythic+ Dungeons"、"Catalyst Season 2"），DB2 里根本没这些 id。
#
# 这些条目在界面上会真的显示出来（制造业装备的来源就是"裁缝"），
# 留着英文对中文玩家没有意义，所以手工译。数量固定、不随版本变，
# 比再去找一遍数据源划算。
MANUAL = {
    "Alchemy": "炼金", "Blacksmithing": "锻造", "Enchanting": "附魔",
    "Engineering": "工程学", "Inscription": "铭文", "Jewelcrafting": "珠宝加工",
    "Leatherworking": "制皮", "Tailoring": "裁缝",

    "Mythic+ Dungeons": "史诗钥石地下城",
    "Normal Dungeons": "普通地下城",
    "Heroic Dungeons": "英雄地下城",
    "Trash Drop": "小怪掉落",

    "Epic Profession Items": "史诗制造业物品",
    "Rare Profession Items": "稀有制造业物品",
    "PVP Profession Items": "PVP 制造业物品",

    "PVP Season 2 (Bloody Tokens)": "PVP 第 2 赛季（血币）",
    "PVP Season 2 (Conquest)": "PVP 第 2 赛季（征服点）",
    "PVP Season 2 (Honor)": "PVP 第 2 赛季（荣誉）",

    "Catalyst Season 1": "催化剂第 1 赛季",
    "Catalyst Season 2": "催化剂第 2 赛季",
    "Catalyst - Midnight Season 2": "催化剂·午夜第 2 赛季",

    "Delves Season 1": "地下堡第 1 赛季",
    "Delves Season 2": "地下堡第 2 赛季",
    "Prey Season 1": "猎杀第 1 赛季",
    "Prey Season 2": "猎杀第 2 赛季",
}


def load_build():
    with io.open(META, encoding="utf-8") as f:
        return json.load(f)["wowBuild"]


def fetch_table(table, build, locale, proxy=None):
    """取 ID -> Name_lang。其余列（Description 之类）全部丢掉。"""
    url = URL.format(table=table, build=build, locale=locale)
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=300) as r:
        raw = r.read()

    # utf-8-sig：wago 的表带 BOM，不处理的话第一列名会变成 "\ufeffID"
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
    header = next(reader)
    id_i, name_i = header.index("ID"), header.index("Name_lang")
    out = {}
    for row in reader:
        if len(row) <= max(id_i, name_i):
            continue
        name = row[name_i].strip()
        if name:
            out[row[id_i].strip()] = name
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", help="下载用的代理，例如 http://127.0.0.1:7897")
    args = ap.parse_args()

    build = load_build()
    print(f"build {build}")

    pairs, by_inst, by_enc = {}, {}, {}

    for table, by_id, label in (("JournalInstance", by_inst, "副本"),
                                ("JournalEncounter", by_enc, "首领")):
        print(f"下载 {table} …")
        en = fetch_table(table, build, "enUS", args.proxy)
        zh = fetch_table(table, build, "zhCN", args.proxy)
        hit = 0
        for k, name_zh in zh.items():
            by_id[k] = name_zh
            name_en = en.get(k)
            if name_en and name_en != name_zh:
                # 同一个英文名出现多次时只留第一条：副本重名的情况下
                # 两条中文其实一样，留哪条都不会错
                if name_en not in pairs:
                    pairs[name_en] = name_zh
                hit += 1
        print(f"  {label} enUS {len(en)} 条 / zhCN {len(zh)} 条，可对照 {hit} 条")

    data = {
        "_note": "副本/首领中英对照，从客户端 DB2 表（wago.tools, locale=enUS+zhCN）"
                 "按 ID 配对生成。主用 pairs（按英文名查中文），因为 "
                 "item_sources.encounter_id 不总是 JournalEncounter.ID，按 id 会错配。",
        "build": build,
        "pairs": dict(sorted(pairs.items())),
        "manual": MANUAL,
        "byInstanceId": by_inst,
        "byEncounterId": by_enc,
    }

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)

    print(f"写出 {OUT}（{os.path.getsize(OUT) / 1024:.0f} KB），"
          f"可对照 {len(pairs)} 组 + 手工 {len(MANUAL)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())

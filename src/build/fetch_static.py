"""拉 Raidbots 的静态数据（装备、副本、赛季…）。

这些是"游戏数据"，每个版本都会变，但格式不变，所以**代码不用改**——
`--update` 就是重跑这个脚本 + 重建库。

为什么用 Raidbots 而不是我们自己解 DB2：Raidbots 的数据和 SimulationCraft 同源，
都是由客户端 DB2 导出的，而且和"导出 simc 去 Raidbots 跑"用同一套物品 id，
两边对得上才不会出现"本地算 334、Raidbots 认为 331"这种鬼故事。

    python src/build/fetch_static.py                 # 拉 live 环境的全部文件
    python src/build/fetch_static.py --proxy ...     # 走代理
    python src/build/fetch_static.py --only meta     # 只拉一个
"""

import argparse
import io
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(ROOT, "data", "raw")
BASE = "https://www.raidbots.com/static/data/live/{}.json"

# build_db 要用到的文件。加新表时把新文件加进来即可，不用动 build_db 的逻辑。
FILES = ("meta", "equippable-items", "instances", "seasons",
         "item-sets", "bonus-upgrade-sets", "currency-types", "bonuses")


def fetch(name, proxy="", timeout=180):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    url = BASE.format(name)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=timeout) as r:
        return r.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description="拉 Raidbots 静态数据")
    ap.add_argument("--proxy", default=os.environ.get("WG_PROXY", ""))
    ap.add_argument("--only", help="只拉一个，例如 meta")
    args = ap.parse_args(argv)

    names = (args.only,) if args.only else FILES
    build = ""
    for name in names:
        path = os.path.join(RAW, f"{name}.json")
        try:
            blob = fetch(name, args.proxy)
        except Exception as e:                                  # noqa: BLE001
            print(f"  ✗ {name}: {e}")
            continue
        tmp = path + ".tmp"
        with io.open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)          # 原子替换：拉到一半断网不会毁掉旧数据
        size = len(blob) / 1048576
        print(f"  ✓ {name}.json  {size:.1f} MB")
        if name == "meta":
            build = json.loads(blob.decode("utf-8")).get("wowBuild") or ""
    if build:
        print(f"  游戏 build：{build}")
        print("  下一步：python main.py --build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

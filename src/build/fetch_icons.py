"""把装备图标下载到本地，供界面离线使用。

图标有两个来源，按速度和使用顺序排列：

1. 暴雪渲染接口 render.worldofwarcraft.com/icons/56/{名字}.jpg
   快（约 1 秒），但只覆盖部分素材——新版本的图标常常是 403。
   注意路径里**不能带区域前缀**（/us/ 只认旧图标）。
2. wago.tools 的 CASC 接口按 FileDataID 取游戏原始 BLP，
   再用 src/icons.py 解码成 PNG。能覆盖全部图标，但很慢、偶发失败。
   必须加 --http1.0，否则 HTTP/2 连接不关闭，curl 会一直等到超时。

因此策略是：先用 1 快速刷一遍，1 失败的再用 2 补漏。

用法：
    python src/build/fetch_icons.py           # 当前赛季装备用到的图标
    python src/build/fetch_icons.py --all     # 所有装备图标
    python src/build/fetch_icons.py --retry   # 只重试上次失败的
    python src/build/fetch_icons.py --gems    # 宝石图标 -> data/icons/gems/
    python src/build/fetch_icons.py --enchants  # 附魔图标 -> data/icons/enchants/

宝石和附魔的图标分目录放，不并进装备图标里：它们数量少、更新频率不同，
混在一起以后想清缓存分不清谁是谁。文件名沿用图标原名，前端按
`/icon/gems/<名字>` 取。
"""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from icons import blp_to_png  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(ROOT, "data", "wowgear.db")
ICON_ROOT = os.path.join(ROOT, "data", "icons")
ICON_DIR = ICON_ROOT                     # 装备图标，直接放在 icons/ 下
FAIL_LOG = os.path.join(ROOT, "data", "icon_failures.json")

GEM_DIR = os.path.join(ICON_ROOT, "gems")
ENCH_DIR = os.path.join(ICON_ROOT, "enchants")

RENDER = "https://render.worldofwarcraft.com/icons/56/{}.jpg"
CASC = "https://wago.tools/api/casc/{}"


def _curl(args, timeout):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout).stdout
    except Exception:
        return b""


def _has_icon(name, folder=None):
    folder = folder or ICON_DIR
    base = os.path.join(folder, name)
    return os.path.exists(base + ".png") or os.path.exists(base + ".jpg")


def stage_render(name, timeout=8, folder=None):
    """第一轮：暴雪渲染接口，快。"""
    folder = folder or ICON_DIR
    if _has_icon(name, folder):
        return name, "cached"
    data = _curl(["curl", "-s", "--max-time", str(timeout), RENDER.format(name)], timeout + 5)
    # 校验 JPEG 首尾标记，截断的文件会缺 EOI，存下来就是半张图
    if (data and len(data) > 500 and data[:2] == b"\xff\xd8"
            and data[-2:] == b"\xff\xd9"):
        with open(os.path.join(folder, name + ".jpg"), "wb") as f:
            f.write(data)
        return name, "render"
    return name, "miss"


def stage_casc(name, fdid, retries=4, folder=None):
    """第二轮：wago.tools CASC 取原始 BLP 再解码，慢但全覆盖。

    wago.tools 偶发卡住（有时 20 秒只回几百字节），所以重试次数要够，
    单次超时给到 35 秒。--max-time 是硬上限，超时会拿到截断数据，
    下面解码器的长度校验会把这种数据判失败，不会写进磁盘。
    """
    folder = folder or ICON_DIR
    if _has_icon(name, folder):
        return name, "cached"
    if not fdid:
        return name, "no_fdid"
    for _ in range(retries):
        blp = _curl(["curl", "-sL", "--http1.0", "--max-time", "35", CASC.format(fdid)], 45)
        if blp and len(blp) > 200 and blp[:4] == b"BLP2":
            png = blp_to_png(blp)
            if png and len(png) > 500:   # 太小的多半是纯色空图，判失败重试
                with open(os.path.join(folder, name + ".png"), "wb") as f:
                    f.write(png)
                return name, "casc"
    return name, "fail"


def run(rows, folder, label):
    """跑两轮抓取。rows 是 [(图标名, FileDataID)]。"""
    os.makedirs(folder, exist_ok=True)
    print(f"{label}：待处理 {len(rows)} 个")
    stat = {}

    print("第一轮：暴雪渲染接口 …")
    # 并发不能高：这个接口对密集请求会返回 403，2~3 并发是稳定值
    with ThreadPoolExecutor(max_workers=3) as pool:
        for fut in as_completed([pool.submit(stage_render, n, 8, folder)
                                 for n, _ in rows]):
            _, s = fut.result()
            stat[s] = stat.get(s, 0) + 1
    print(f"  {stat}")

    pending = [(n, f) for n, f in rows if not _has_icon(n, folder)]
    print(f"第二轮：CASC 补漏 {len(pending)} 个 …")
    failures, done = [], 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(stage_casc, n, f, 4, folder): (n, f)
                   for n, f in pending}
        for fut in as_completed(futures):
            name, s = fut.result()
            stat[s] = stat.get(s, 0) + 1
            if s in ("fail", "no_fdid"):
                failures.append(futures[fut])
            done += 1
            if done % 40 == 0:
                print(f"  {done}/{len(pending)}  {stat}")

    print(f"  {label}结果：{stat}")
    files = [f for f in os.listdir(folder) if f.endswith((".png", ".jpg"))]
    total = sum(os.path.getsize(os.path.join(folder, f)) for f in files)
    print(f"  {label}本地 {len(files)} 个，合计 {total / 1024 / 1024:.1f} MB")
    return failures


def _asset_rows(db, table, where="1=1"):
    """从 gems / enchants 表取 (图标名, FileDataID)。"""
    return db.execute(f"""
        SELECT DISTINCT x.icon, ic.file_data_id
          FROM {table} x LEFT JOIN icons ic ON ic.name = LOWER(x.icon)
         WHERE x.icon IS NOT NULL AND x.icon != '' AND {where}
    """).fetchall()


def main():
    want_gems = "--gems" in sys.argv
    want_ench = "--enchants" in sys.argv

    if want_gems or want_ench:
        db = sqlite3.connect(DB_PATH)
        if want_gems:
            run(_asset_rows(db, "gems"), GEM_DIR, "宝石")
        if want_ench:
            run(_asset_rows(db, "enchants"), ENCH_DIR, "附魔")
        db.close()
        return

    os.makedirs(ICON_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)

    if "--retry" in sys.argv and os.path.exists(FAIL_LOG):
        with open(FAIL_LOG, encoding="utf-8") as f:
            names = [n for n, _ in json.load(f)]
        fmap = {r[0]: r[1] for r in db.execute("SELECT name, file_data_id FROM icons")}
        rows = [(n, fmap.get(n.lower())) for n in names]
    elif "--all" in sys.argv:
        rows = db.execute("""SELECT DISTINCT i.icon, ic.file_data_id FROM items i
            LEFT JOIN icons ic ON ic.name = LOWER(i.icon) WHERE i.icon IS NOT NULL""").fetchall()
    else:
        rows = db.execute("""SELECT DISTINCT i.icon, ic.file_data_id FROM items i
            LEFT JOIN icons ic ON ic.name = LOWER(i.icon)
            WHERE i.is_current = 1 AND i.icon IS NOT NULL""").fetchall()
    db.close()

    failures = run(rows, ICON_DIR, "装备")
    with open(FAIL_LOG, "w", encoding="utf-8") as f:
        json.dump(failures, f, ensure_ascii=False)
    if failures:
        print(f"\n仍有 {len(failures)} 个失败，清单见 {FAIL_LOG}（可用 --retry 重试）")


if __name__ == "__main__":
    main()

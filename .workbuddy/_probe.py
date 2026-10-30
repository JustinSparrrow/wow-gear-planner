import sys, io
sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8")
from wowlib import talents as T
table = T.load_table()
hunter = table[3]
tstr = "C8PAD57yiELKEty14ekTDtZEqMWwMzYmZb0stMMDzMNYxMzMzMjlBAAAAAgZMzMjtZMmZmBjpZAAAAGAMssMz8ALMzMMMGwMbzYDMGzMbGAA"

def run(nodes, label):
    try:
        spec, picked = T.decode(tstr, 3, table={3: nodes})
        hero = [p["name"] for p in picked if p["tree"] == "hero"]
        print(f"{label}: 选中 {len(picked)} 个，hero {len(hero)} 个 -> {hero[:6]}")
    except Exception as e:
        print(f"{label}: 失败 {e}")

run(hunter, "全部节点")
# 只保留 255 专精可用的条目
def filt(nodes, spec=255):
    out = {}
    for nid, entries in nodes.items():
        keep = [e for e in entries if not e["spec"] or spec in e["spec"]]
        if keep:
            out[nid] = keep
    return out
run(filt(hunter), "只留 255 可用")
# 只留 class + spec 树
run({n: e for n, e in filt(hunter).items() if all(x["tree"] != "hero" for x in e)}, "去掉 hero 树")

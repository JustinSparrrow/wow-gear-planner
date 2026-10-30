"""图标质检：找出残缺、纯色、尺寸不对的图片。

历史上出现过两类"看起来有文件、其实是坏的"图标：

1. **JPEG 被截断**——缺 EOI 结束标记，只能显示上半截。
2. **BLP 被截断后解码**——这是最隐蔽的一类。DXT 数据不够时，解码器会把剩下的
   像素留成透明，于是产出一张"上半截有画、下半截空白"的图。文件本身是合法 PNG，
   IEND 齐全、像素数据长度也吻合，只看结构完全查不出来，必须看**内容**。

判断依据：DXT 按 4×4 像素块解码，截断一定发生在 4 行对齐处。所以如果
「内容从第 0~2 行开始」+「最后一行有内容的行号 ≡ 3 (mod 4)」+「底部至少空 4 行」，
就是截断特征。真实图标的底边是剪影形状，几乎不会正好齐平在 4 的倍数行上。

用法：
    python src/check_icons.py            # 检查并列出问题
    python src/check_icons.py --fix      # 删除坏图并写入失败清单，供 --retry 重下
"""

import json
import os
import struct
import sys
import zlib
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
ICON_DIR = os.path.join(ROOT, "data", "icons")
FAIL_LOG = os.path.join(ROOT, "data", "icon_failures.json")
PNG_SIG = b"\x89PNG\r\n\x1a\n"

# 游戏图标是 64x64（CASC）或 56x56（渲染接口），留一点余量
MIN_SIZE, MAX_SIZE = 32, 256
ALPHA_MIN = 8          # 低于这个 alpha 视为透明，避免 DXT 边缘噪点干扰
BLANK_PCT = 10         # 不透明像素占比低于此值判为空白图


def parse_png(data):
    """拆 PNG 块，返回 dict(w,h,ctype,depth,idat,complete)。"""
    if data[:8] != PNG_SIG:
        return None
    pos, w, h, ctype, depth, idat, complete = 8, None, None, None, None, [], False
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        if len(payload) < length:
            break  # 块被截断
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", payload[:10])
        elif tag == b"IDAT":
            idat.append(payload)
        elif tag == b"IEND":
            complete = True
            break
        pos += 12 + length
    return {"w": w, "h": h, "depth": depth, "ctype": ctype,
            "idat": b"".join(idat), "complete": complete}


def unfilter(raw, w, h, bpp):
    """还原 PNG 的行过滤器，返回按行排列的像素字节。"""
    stride = w * bpp
    out, prev = [], bytearray(stride)
    for y in range(h):
        base = y * (stride + 1)
        f = raw[base]
        line = bytearray(raw[base + 1:base + 1 + stride])
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out.append(line)
        prev = line
    return out


def parse_jpeg(data):
    """返回 dict(w,h,complete)。"""
    if data[:2] != b"\xff\xd8":
        return None
    complete = data[-2:] == b"\xff\xd9"
    i, w, h = 2, None, None
    while i < len(data) - 3:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            break
        i += 2 + seglen
    return {"w": w, "h": h, "complete": complete}


def png_metrics(p):
    """算内容指标。返回 dict(rows,first,last,pct)，无像素时返回 None。"""
    w, h, ctype = p["w"], p["h"], p["ctype"]
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype, 4)
    raw = zlib.decompress(p["idat"])
    if len(raw) < h * (1 + w * bpp):
        return None
    rows = unfilter(raw, w, h, bpp)
    filled = []
    for line in rows:
        if bpp == 4:
            hit = any(line[x * 4 + 3] > ALPHA_MIN for x in range(w))
        else:
            hit = any(abs(line[x * bpp] - 255) > ALPHA_MIN or True for x in range(w))
        filled.append(hit)
    if not any(filled):
        return {"rows": 0, "first": -1, "last": -1, "pct": 0}
    first = filled.index(True)
    last = h - 1 - filled[::-1].index(True)
    if bpp == 4:
        opaque = sum(1 for line in rows for x in range(w) if line[x * 4 + 3] > ALPHA_MIN)
    else:
        opaque = w * (last - first + 1)
    return {"rows": last - first + 1, "first": first, "last": last,
            "pct": opaque * 100 // (w * h)}


def check(path):
    """返回 (状态, 说明)。状态 ok 表示可用。"""
    size = os.path.getsize(path)
    if size < 300:
        return "tiny", f"文件仅 {size} 字节"

    with open(path, "rb") as f:
        data = f.read()

    if path.endswith(".png"):
        p = parse_png(data)
        if not p:
            return "bad_png", "不是合法 PNG"
        if not p["complete"]:
            return "truncated", "缺少 IEND，文件被截断"
        if not p["w"] or not p["h"]:
            return "bad_png", "缺少 IHDR"
        if not (MIN_SIZE <= p["w"] <= MAX_SIZE and MIN_SIZE <= p["h"] <= MAX_SIZE):
            return "size", f"尺寸异常 {p['w']}x{p['h']}"
        try:
            m = png_metrics(p)
        except Exception as e:
            return "corrupt", f"像素数据无法解析（{e}）"
        if not m:
            return "short_data", "像素数据不足"
        if m["pct"] < BLANK_PCT:
            return "blank", f"几乎全透明（不透明仅 {m['pct']}%）"
        # 截断特征：内容贴顶 + 底边正好落在 4 的倍数行 + 底部留空
        if (m["first"] <= 2 and m["last"] % 4 == 3 and m["last"] < p["h"] - 4):
            return "cut", f"内容只到第 {m['last']} 行（共 {p['h']} 行），疑似截断"
        return "ok", ""

    if path.endswith(".jpg"):
        j = parse_jpeg(data)
        if not j:
            return "bad_jpg", "不是合法 JPEG"
        if not j["complete"]:
            return "truncated", "缺少 EOI，文件被截断"
        if not j["w"] or not j["h"]:
            return "bad_jpg", "解析不到尺寸"
        if not (MIN_SIZE <= j["w"] <= MAX_SIZE and MIN_SIZE <= j["h"] <= MAX_SIZE):
            return "size", f"尺寸异常 {j['w']}x{j['h']}"
        return "ok", ""

    return "unknown", "未知格式"


def main():
    files = sorted(f for f in os.listdir(ICON_DIR) if f.endswith((".png", ".jpg")))
    print(f"共 {len(files)} 个图标文件\n")

    stat, problems = Counter(), []
    for f in files:
        s, msg = check(os.path.join(ICON_DIR, f))
        stat[s] += 1
        if s != "ok":
            problems.append((f, s, msg))

    print("结果：")
    for k, v in stat.most_common():
        mark = "✓" if k == "ok" else "✗"
        print(f"  {mark} {k:12} {v}")

    if problems:
        print(f"\n有问题的 {len(problems)} 个：")
        for f, s, msg in problems[:50]:
            print(f"  [{s:9}] {f:50} {msg}")
        if len(problems) > 50:
            print(f"  …另有 {len(problems) - 50} 个")

    if "--fix" in sys.argv and problems:
        names = sorted({f.rsplit(".", 1)[0] for f, s, _ in problems})
        existing = []
        if os.path.exists(FAIL_LOG):
            with open(FAIL_LOG, encoding="utf-8") as fp:
                existing = [n for n, _ in json.load(fp)]
        merged = sorted(set(names) | set(existing))
        with open(FAIL_LOG, "w", encoding="utf-8") as fp:
            json.dump([[n, None] for n in merged], fp, ensure_ascii=False)
        # 必须删掉坏文件，否则 --retry 会当成已存在而跳过
        for f, s, _ in problems:
            os.remove(os.path.join(ICON_DIR, f))
        print(f"\n已删除 {len(problems)} 个坏文件，并写入失败清单"
              f"（共 {len(merged)} 个待重下）")
        print("接着运行：python src/fetch_icons.py --retry")


if __name__ == "__main__":
    main()

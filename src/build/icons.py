"""把魔兽世界的 BLP2 图标解码成 PNG。

为什么自己写：环境里没有 Pillow，也没有 ImageMagick，而 BLP 是暴雪私有格式。
图标尺寸固定 64x64，压缩方式绝大多数是 DXT1/DXT5，解码逻辑不长，自己实现最省事。

BLP2 头部布局（wowdev wiki）：
    0   magic        "BLP2"
    4   type         uint32，1 = 直接存储
    8   compression  uint8，0=JPEG 1=调色板 2=DXT 3=BGRA
    9   alphaDepth   uint8
    10  alphaType    uint8，0/1=DXT1 系的 1bit，7=DXT5 的 8bit
    11  hasMips      uint8
    12  width        uint32
    16  height       uint32
    20  mipOffsets   uint32[16]
    84  mipSizes     uint32[16]
"""

import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _rgb565(v):
    r = ((v >> 11) & 0x1F) * 255 // 31
    g = ((v >> 5) & 0x3F) * 255 // 63
    b = (v & 0x1F) * 255 // 31
    return r, g, b


def _decode_dxt_color(block, force_four_color, out, ow, x0, y0, alpha_map=None):
    """解一个 4x4 颜色块，写入 out(RGBA bytearray)。"""
    c0, c1 = struct.unpack_from("<HH", block, 0)
    bits = struct.unpack_from("<I", block, 4)[0]
    p0, p1 = _rgb565(c0), _rgb565(c1)

    if force_four_color or c0 > c1:
        palette = [
            (*p0, 255),
            (*p1, 255),
            tuple((2 * p0[i] + p1[i]) // 3 for i in range(3)) + (255,),
            tuple((p0[i] + 2 * p1[i]) // 3 for i in range(3)) + (255,),
        ]
    else:
        palette = [
            (*p0, 255),
            (*p1, 255),
            tuple((p0[i] + p1[i]) // 2 for i in range(3)) + (255,),
            (0, 0, 0, 0),
        ]

    for py in range(4):
        for px in range(4):
            idx = (bits >> (2 * (py * 4 + px))) & 0x3
            r, g, b, a = palette[idx]
            X, Y = x0 + px, y0 + py
            if X >= ow:
                continue
            o = (Y * ow + X) * 4
            out[o:o + 4] = bytes((r, g, b, a))


def _decode_dxt5_alpha(block, out, ow, x0, y0):
    """DXT5 的 8 字节 alpha 块：2 个端点 + 16 个 3bit 索引。"""
    a0, a1 = block[0], block[1]
    if a0 > a1:
        table = [a0, a1] + [
            ((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)
        ]
    else:
        table = [a0, a1] + [
            ((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)
        ] + [0, 255]

    bits = int.from_bytes(block[2:8], "little")
    for py in range(4):
        for px in range(4):
            i = py * 4 + px
            idx = (bits >> (3 * i)) & 0x7
            a = table[min(idx, len(table) - 1)]
            X, Y = x0 + px, y0 + py
            if X >= ow:
                continue
            out[(Y * ow + X) * 4 + 3] = a


def decode(blp):
    """解码 BLP2 数据，返回 (width, height, rgba_bytes) 或 None。"""
    if len(blp) < 148 or blp[:4] != b"BLP2":
        return None
    comp, alpha_depth, alpha_type = blp[8], blp[9], blp[10]
    w, h = struct.unpack_from("<II", blp, 12)
    mip_off = struct.unpack_from("<16I", blp, 20)
    mip_size = struct.unpack_from("<16I", blp, 84)

    if w == 0 or h == 0 or w > 512 or h > 512:
        return None
    data = blp[mip_off[0]: mip_off[0] + mip_size[0]]

    if comp == 3:  # 未压缩 BGRA
        out = bytearray(len(data))
        for i in range(0, len(data) - 3, 4):
            out[i:i + 4] = bytes((data[i + 2], data[i + 1], data[i], data[i + 3]))
        return w, h, bytes(out)

    if comp != 2:  # JPEG / 调色板暂不支持
        return None

    out = bytearray(w * h * 4)
    # alphaType 7 = DXT5（8bit alpha），1 = DXT3，0/其他 = DXT1
    is_dxt5 = alpha_type == 7
    is_dxt3 = alpha_type == 1
    block_size = 16 if (is_dxt5 or is_dxt3) else 8
    bx_count = (w + 3) // 4

    # 完整性校验：下载被截断时，数据会短于所需，直接判失败。
    # 否则会静默产出一张全透明的图，看起来像"有图标"，实际是空的。
    expected = bx_count * ((h + 3) // 4) * block_size
    if len(data) < expected * 0.95:
        return None

    pos = 0
    for by in range((h + 3) // 4):
        for bx in range(bx_count):
            if pos + block_size > len(data):
                break
            blk = data[pos: pos + block_size]
            x0, y0 = bx * 4, by * 4
            if is_dxt5:
                _decode_dxt5_alpha(blk[:8], out, w, x0, y0)
                _decode_dxt_color(blk[8:16], True, out, w, x0, y0)
            elif is_dxt3:
                for py in range(4):
                    for px in range(4):
                        X, Y = x0 + px, y0 + py
                        if X >= w:
                            continue
                        nib = (blk[py * 4 + px // 2] >> (0 if px % 2 == 0 else 4)) & 0xF
                        out[(Y * w + X) * 4 + 3] = nib * 17
                _decode_dxt_color(blk[8:16], True, out, w, x0, y0)
            else:
                _decode_dxt_color(blk[:8], False, out, w, x0, y0)
            pos += block_size
    return w, h, bytes(out)


def _chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def to_png(w, h, rgba):
    """把 RGBA 原始像素打包成 PNG 字节流。"""
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return (PNG_SIG
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6))
            + _chunk(b"IEND", b""))


def blp_to_png(blp_bytes):
    r = decode(blp_bytes)
    if not r:
        return None
    w, h, rgba = r
    return to_png(w, h, rgba)


if __name__ == "__main__":
    import sys
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, "rb") as f:
        out = blp_to_png(f.read())
    if not out:
        raise SystemExit("解码失败")
    with open(dst, "wb") as f:
        f.write(out)
    print(f"{src} -> {dst}  {len(out)} bytes")

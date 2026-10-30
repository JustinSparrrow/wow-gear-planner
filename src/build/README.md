# src/build — 构建期脚本

离线跑、不进运行时。这一层的脚本只在"改数据"时用，跑完产出的都是 `data/` 下的静态文件。

**和运行时分开放**是有意的：混在 `src/` 里会让人以为"改一改就要重启服务"。

## 文件

| 文件 | 作用 | 产出 |
|---|---|---|
| `build_db.py` | 建库（主入口） | `data/wowgear.db` |
| `versions.py` | 数据集版本清单、过期检测 | `data/versions.json` |
| `fetch_static.py` | 从 Raidbots 拉装备/副本/赛季静态数据 | `data/raw/*.json`（53MB） |
| `fetch_icons.py` | 批量下载图标 | `data/icons/`（装备）+ `gems/` + `enchants/` |
| `fetch_gems_enchants.py` | 抓宝石/附魔数值 + 补图标名；`--reparse` 离线重算并补腿部 | `data/raw/gems.json`、`enchants.json` |
| `fetch_names.py` | 从客户端 ItemSparse 抽中文名 | `data/raw/names_zhcn.json` |
| `fetch_sockets.py` | 抓装备插槽数 | `data/raw/sockets.json` |
| `fetch_class_base.py` | 从 SimC 生成的 gt 表抽裸装基础属性 | `data/raw/class_base.json` |
| `fetch_talents.py` | 从 SimC 的 trait 表抽天赋节点表 | `data/raw/talents.json` |
| `icons.py` | BLP2 解码器（被 fetch_icons 调） | — |
| `check_icons.py` | 图标体检（找坏图） | 重试清单 |
| `tiers.py` | 套装转化源反查 | 终端输出 |
| `manage_items.py` | 手工加自定义装备 | 写进 `wowgear.db` |

## 依赖关系

```
build_db.py ──读──> data/raw/{equippable-items,seasons,instances,...}.json
                     + gems.json + enchants.json + sockets.json
                     + names_zhcn.json + class_base.json
                     + spec_mastery.json + rating_curves.json
                     + spec_stats.json
                   → data/wowgear.db

versions.py ──读──> data/versions.json + wago.tools/api/builds
                     每个数据集记着 build / source / updated
                  → 告诉 --update 哪些要重拉

fetch_static.py ──拉──> Raidbots 静态数据
                      → data/raw/{equippable-items,bonuses,seasons,instances,...}.json

fetch_icons.py ──查──> wowgear.db 里的图标名 + FileDataID
                   → data/icons/*.png|jpg

fetch_gems_enchants.py ──读──> permanent-enchant.inc + .item-sparse.csv
                              + wowhead tooltip（要翻墙代理）
                          → data/raw/{gems,enchants}.json

fetch_sockets.py ──查──> wowhead tooltip（要翻墙代理）
                    → data/raw/sockets.json

fetch_names.py ──读──> ItemSparse 的 zhCN CSV（wago.tools，48MB，抽完删）
                        + equippable-items.json / gems.json / enchants.json 里的 id
                    → data/raw/names_zhcn.json

fetch_class_base.py ──读──> SimC 仓库 engine/dbc/sc_extra_data.inc（zip 里直接取）
                        → data/raw/class_base.json

fetch_talents.py ──读──> SimC 的 trait_data.inc
                      → data/raw/talents.json（只有节点表，没有效果表）

build_db.py 还会读 names_zhcn.json，把中文名写进 items.name_cn、
gems.name_cn/ilvl、enchants.name_cn。缺这个文件只是界面退回英文名，建库不会失败。
```

## 版本管理（`versions.py`）

管的是「哪个数据集对应哪个游戏版本」，不是代码版本。

```bash
python main.py --check-update      # 对比 wago 上的最新 build
python main.py --update            # 只重拉过期的，然后重建库
python src/build/versions.py --check
python src/build/versions.py --mark items 12.1.5.70575   # 手工记一个版本
```

**过期判据是「大版本」**（`12.1` vs `12.2`），不是精确 build 号：
12.1.5 → 12.1.7 这种小补丁，装备数据通常还能用，只会少几件新装备；跨大版本才算真换代。
这条规则在 `versions.major()` 里，是整个更新流程唯一的判断依据。

`--update` 时如果 `--web` 服务开着，SQLite 会被锁住（Windows 直接 PermissionError），
脚本会提前提示先关服务，而不是甩一堆 traceback。

⚠️ `spec_mastery` 和 `rating_curves` 两个数据集的 `script` 是 `None`——它们需要人工核对
（客户端法术表 / Maxroll），没有全自动脚本。

`spec_stats.json` **连版本清单里都没有**，因为它不是"能拉下来的数据集"，而是**观测值**：
靠真实游戏面板反推出来的每专精基线，没有接口能给。它的失效方式也不一样——
不是"有新版本了要重下"，而是"天赋改版了这些数就错了"。所以它自己在文件头记了 `build`，
`load_spec_stats()` 建库时会跟 `meta.json` 的 `wowBuild` 比对，不一致就打警告。

## 翻墙代理

`fetch_gems_enchants.py`、`fetch_sockets.py`、`fetch_class_base.py`（GitHub）要访问境外站点。
代理优先取 `--proxy` 参数，其次 `WG_PROXY` 环境变量：

```bash
python src/build/fetch_sockets.py --proxy http://127.0.0.1:7897
```

`fetch_icons.py` 走 wago.tools，**不需要**代理，但 CASC 接口偶发卡住，所以重试次数给到
4 次、单次 35 秒，并且必须加 `--http1.0`（走 HTTP/2 连接不关闭，curl 一直等到超时）。

## 建库流程（`main.py --build` 封装了这些）

1. 读 `data/raw/` 下的 Raidbots 静态数据
2. 按当前赛季筛选（见根 README「只留当前赛季」）
3. 算插槽、归类宝石、载入附魔、中文名、裸装基础属性、精通系数、换算曲线
4. 写 `wowgear.db`（会先删掉旧的）

⚠️ 建库会重建整个 `wowgear.db`，但**不碰** `accounts.db`——账号、历史、配装存档不受影响。

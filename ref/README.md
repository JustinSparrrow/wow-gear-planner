# ref — SimulationCraft 源码片段

查公式时参考用的 SimC 源码，**不是本项目的依赖**，只是摘过来对照的。

| 文件 | 内容 | 什么时候看 |
|---|---|---|
| `data_enums.hh` | 属性/槽位/物品类型的枚举定义 | 对 `const.py` 里的 ID 有疑问时 |
| `item.cpp` | 装备解析与属性计算逻辑 | 查属性换算公式、`redirected_base_stats` 行为 |
| `item_data.cpp` / `item_data.hpp` | 装备数据表结构 | 查 `bonus_id`、`gem_id`、`enchant_id` 怎么解析 |
| `item_database.hpp` | 装备数据库接口 | 查 `RandPropPoints` 预算表结构 |

## 为什么留这些

核心算法（属性曲线、装等轨道、套装转化）都是对照 SimC 源码逆向出来的。留一份在
仓库里，以后公式变了或想再核对，不用去 GitHub 翻。这些是 `.hh`/`.cpp`，不会被
Python 运行到，纯参考。

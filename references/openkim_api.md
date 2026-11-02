# OpenKIM REST API — 交叉检索参考

`search_potentials.py --source openkim` 的底层接口（2026-11 实测）。
OpenKIM（https://openkim.org）与 NIST 库互补：NIST 给参数文件直链，OpenKIM 给模型码
（`kim-api` 本地安装后 LAMMPS 直接 `pair_style kim` 调用）。

## 1. 文档位置

- 查询界面与完整文档：https://query.openkim.org/
- 官方说明：https://openkim.org/doc/usage/kim-query/ （注意 `/doc/programming/kim-query/` 是 404）
- Python 客户端：PyPI `kim-query`（v4.0.0），本 skill 未依赖（纯 stdlib 实现）。

## 2. get_available_models（主脚本所用端点）

```
POST https://query.openkim.org/api/get_available_models
Content-Type: application/x-www-form-urlencoded
```

参数（**每个值都是双引号 JSON 字符串**，form-encoded）：

| 参数 | 示例 | 说明 |
|---|---|---|
| `species` | `["Cu","Ni"]` | 元素列表 |
| `species_logic` | `["and"]` 或 `["exact"]` | **只有这两种**，无 `"or"`（实测报错） |
| `model_interface` | `["any"]` | 或 `["table"]` 等具体接口 |
| `simulator_name` | `["LAMMPS"]` | 可选；不传则含全部模拟器 |
| `potential_type` | `["meam"]` | 可选；按势类型过滤 |

返回：模型码字符串数组（或 dict 数组，视版本），形如
`EAM_Dynamo_FoilesBaskesDaw_1986Universal3_Cu__MO_666348409573_004`。
模型页 = `https://openkim.org/id/<模型码>`。

## 3. 通用 raw 查询（备用）

```
POST https://query.openkim.org/api
database=obj  &  query={"type":"mo","species":"Al"}  &  fields={"kimcode":1}  &  limit=4
```

- `database` 只能是 `obj / data / job / log / agent`，传别的报 "Empty queries not allowed"。
- `query`/`fields` 必须双引号 JSON（Python 单引号风格直接报 "invalid query"）。

## 4. 模型文件的获取方式（与 NIST 不同！）

OpenKIM 模型**没有裸 tarball 直链**（`s3.openkim.org/archives/mo/...` 实测 404），
标准获取方式是 kim-api 命令：

```
kim-api-collections-management install user EAM_Dynamo_FoilesBaskesDaw_1986Universal3_Cu__MO_666348409573_004
```

LAMMPS 内也可用 `kim_query` 命令在线取。批量快照：`https://s3.openkim.org/archives/collection/`。

## 5. NIST ↔ OpenKIM 对应

NIST 条目 JSON 的 `implementation[]` 中 `type=="OpenKIM"` 项内嵌 openkim.org 链接
（如 `https://openkim.org/id/EAM_Dynamo_Foiles_1985_Cu__MO_831121933939_000`），
主脚本在详情输出里已展示，两条数据源可互相跳转验证。

# NIST Interatomic Potentials Repository — 机器访问参考

本 skill 的数据源与实测结论（2026-11 验证）。主脚本 `search_potentials.py` 已封装以下全部逻辑；
此文档供人工排查与扩展时参考。

## 1. 官方渠道现状

- 主站：https://www.ctcms.nist.gov/potentials/ （DOI: 10.18434/m37）
- **没有**单一 JSON 总索引：`https://www.ctcms.nist.gov/potentials/repository-index.json` → 404。
- `potentials.nist.gov`（官方 `potentials` Python 包的后端）裸 HTTPS GET 会超时（需 pymongo 路径），
  勿在脚本里依赖裸 REST；需要时 `pip install potentials`（usnistgov/potentials）作为兜底。
- 官方 HTML entry 页（`.../entry/<entry-id>/<impl-id>.html`）**不含**显式 pair_style/pair_coeff 命令行，
  只有类型标注与文件链接。

## 2. 实际数据底座：lmhale99/potentials-library（全库 GitHub 镜像）

NIST 人员 Laura Hale 维护的全库镜像，与 potentials.nist.gov 同源。raw 路径前缀：

```
https://raw.githubusercontent.com/lmhale99/potentials-library/master
```

| 文件 | 内容 |
|---|---|
| `Potential.csv` | 951 条全库索引：`id / elements / fictionalelements`（python-repr 列表，用 `ast.literal_eval`）等 |
| `potential_LAMMPS.csv` | 716 条 LAMMPS 实现索引：`id / potid / pair_style / units / atom_style / symbols / elements / artifacts(文件名+直链) / dois / status` |
| `Potential/potential.<entry-id>.json` | 势条目记录（引用 / DOI / bibtex / implementation） |
| `potential_LAMMPS/<impl-id>.json` | LAMMPS 实现记录（结构化 pair_style / pair_coeff / units / atom_style / artifact） |

pair_style 分布（potential_LAMMPS.csv）：meam 281、eam/alloy 151、eam/fs 111、adp 30、
tersoff 21、hybrid/overlay 18、eam 17、bop 11、vashishta 8、sw 5、mlip 5、reax/c 4 ……

## 3. JSON schema 要点（含已踩过的变体）

### Potential/potential.<id>.json

```
interatomic-potential:
  id / key / URL / record-version
  description.citation:  title / author[{given-name,surname}] / publication-name /
                         publication-date.year / volume / issue / pages / DOI / bibtex
  implementation[]:      { type: "LAMMMS pair_style eam" | "OpenKIM" | ...,
                           artifact: {web-link:{URL, link-text}} 或 [同上列表]（多文件 setfl）,
                           link: {web-link:{URL,...}}（外部链接）, notes.text, id, status, date }
  element:               "Cu"（单元素字符串）或 ["Ni","Cu"]（列表）
```

变体坑：
- `implementation[]` 里个别条目混有**纯字符串项**（非 dict），遍历前必须 `isinstance(imp, dict)`。
- `publication-date` 一般是 dict，但要防个别为字符串。
- `artifact` 可为 dict 或 dict 列表。

### potential_LAMMPS/<impl-id>.json（顶层键 `potential-LAMMPS`）

```
key / id / URL / potential(父条目链接) / comments(官方页面 URL) /
units / atom_style / pair_style / pair_coeff / artifact
```

`pair_style` 两种形态：`{"type": "eam"}` 或 `{"type":"eam/alloy"}`（也可能纯字符串）。
`pair_coeff` 已见三种变体：

```
A. [{interaction:{symbol:["Cu","Cu"]}, term:{file:"Cu_smf7.eam"}}, ...]   (eam funcfl 多文件)
B. {"term": [{"file":"Cu_Ni.eam.alloy"}, {"symbols": true}]}              (setfl 单行 + 元素标志)
C. {"term": {"file": "..."}} / [{"term": {...}}, ...]
```

`symbols: true` 表示"元素取本实现的 symbols 列"（potential_LAMMPS.csv 的 `symbols` 列）。

## 4. 下载直链（实测 200）

```
https://www.ctcms.nist.gov/potentials/Download/<entry-id>/<序号>/<文件名>
例：https://www.ctcms.nist.gov/potentials/Download/1985--Foiles-S-M--Ni-Cu/1/Cu_smf7.eam
```

**TLS 坑**：部分 Python/OpenSSL 组合（本机 Python 3.14 实测）对 www.ctcms.nist.gov
握手报 `SSL: UNEXPECTED_EOF_WHILE_READING`，而系统 curl 正常。主脚本 `http_get()`
已实现 urllib → curl 的自动兜底，无需手工干预。

## 5. pair_style / pair_coeff 渲染规则（主脚本实现）

权威来源是 potential-LAMMPS 结构化记录（不是官方 HTML）。规则与 input_build 约定一致：

| 记录形态 | 渲染结果 |
|---|---|
| `eam` funcfl 单文件 | `pair_style eam` + `pair_coeff * * <file>`（无元素符号） |
| `eam` funcfl 多文件 | 每行 `pair_coeff * * <file> <El>` |
| `eam/alloy`、`eam/fs`、`adp` | `pair_coeff * * <file> <El1> <El2> ...`（元素符号必须有） |
| `meam` | 模板 `pair_coeff * * library.meam <El...> <param>.meam <El...>`，标注"现代 LAMMPS 推荐 meam/c，需核对" |
| 其余（tersoff/sw/reax/pace/hybrid…） | 只给文件清单 + 官方页面链接，标注"需人工核对" |

**never-invent-potentials 红线**：渲染结果只作候选展示；写入任何输入文件前必须经用户确认。

## 6. 官方 Python 包兜底（可选）

- `pip install potentials`（usnistgov/potentials，PyPI 0.4.2+）：
  `Database().get_lammps_potentials(...)` / `download_lammps_potentials(...)`。
- `atomman`（同团队）继承全部数据库功能。两者都直连 potentials.nist.gov（pymongo 路径），
  与本 skill 的镜像 CSV 路线互补。

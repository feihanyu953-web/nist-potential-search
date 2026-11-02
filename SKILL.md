---
name: nist-potential-search
description: 根据模型文件（LAMMPS 输入/data、POSCAR、CIF、extxyz）或显式元素列表，检索 NIST Interatomic Potentials Repository 中匹配体系的势函数条目，展示候选（引用/DOI/pair_style/pair_coeff/参数文件直链），经用户确认后下载参数文件并生成 sha256 清单；可选 OpenKIM 交叉检索。当用户要求"找势函数/势文件/defining pair_style 该用什么文件/查 NIST 势"或 input_build 流程中需要为 LAMMPS pair_style/pair_coeff 挑选经典势文件时使用。不解析 MACE/DeepMD 二进制模型，不自动修改任何输入文件。
---

# nist-potential-search — NIST 势函数检索

## Overview

给定体系元素（从模型文件提取或用户显式给出），到 NIST Interatomic Potentials Repository
（经全库 GitHub 镜像 `lmhale99/potentials-library`）检索匹配的势函数条目，输出候选表 +
逐条详情（文献引用、DOI、`pair_style`/`pair_coeff`、参数文件直链、OpenKIM 对应条目），
并在用户确认后下载参数文件（附 sha256 清单）。

数据链路与全部已知坑见 `references/nist_repo_api.md` 与 `references/openkim_api.md`。

## 前置依赖

| 依赖 | 说明 |
|---|---|
| Python 3.8+（纯标准库） | 主脚本零第三方依赖，任何 Python 3.8+ 解释器均可 |
| curl（可选兜底） | 部分环境 urllib 与 www.ctcms.nist.gov TLS 握手失败，脚本自动回落 curl（Win10+ 自带） |
| 网络 | 首次运行下载索引 CSV（约 3.4 MB）到 `~/.zcode/cache/nist-potentials/` |

## HARD GATE（先问再检索）

1. **元素不确定必须先问**：模型文件解析失败（VASP4 POSCAR、Masses 无符号、pair_coeff 全注释等）
   时，脚本会报错退出——此时向用户要元素列表，**不要猜**。
2. **下载前必须确认**：候选表呈现给用户、用户明确选定某条目后才执行 `--download`。
3. **绝不自动写入输入文件**：检索与渲染出的 `pair_style`/`pair_coeff` 只是候选；
   写入 LAMMPS 输入属于 input_build 的职责，且必须经用户确认（never-invent-potentials 规则）。
   funcfl 单文件 → `pair_style eam`（无元素）；setfl → `eam/alloy`/`eam/fs`（必须有元素符号）。

## 工作流

### 1. 提取元素

```bash
python scripts/search_potentials.py --input <模型文件>
```

支持：LAMMPS `.in/.lammps/.lmp`（读 pair_coeff 元素列表；无则跟随 `read_data` 解析 Masses）、
LAMMPS `.data`、POSCAR（VASP5 格式）、`.cif`、`.xyz/.extxyz`。
ML 势输入（deepmd/mace）会提示：NIST 以经典势为主，结果作对照/基准候选。

### 2. 检索与呈现

```bash
python scripts/search_potentials.py --elements Cu Ni
# 选项：--exact（体系完全一致） / --limit N / --detail N / --include-fictional / --refresh（更新缓存）
```

输出：候选表（条目 id / pair_style / 体系元素）+ 前 N 条详情（引用、DOI、可复制命令块、文件直链）。
- 零命中时自动回退为"共享元素最多的最近邻候选"（多元素复杂体系常见，如 Cs|H|N|O|Pt 体系）。
- 呈现给用户后，给出**选型建议**：优先精确匹配体系 → 看年份与期刊 → meam/eam 族可自动渲染命令，
  tersoff/reax 等需人工核对（脚本已标注）。

### 3. 单条目详情 / 下载

```bash
# 详情（含 bibtex）
python scripts/search_potentials.py --entry 1985--Foiles-S-M--Ni-Cu --bibtex
# 下载（entry id 或实现 id 均可），生成 sha256 清单 nist_manifest.md
python scripts/search_potentials.py --entry 1985--Foiles-S-M--Ni-Cu--LAMMPS--ipr1 --download ./potentials/
```

`--entry` 接受势条目 id（`1985--Foiles-S-M--Ni-Cu`）或 LAMMPS 实现 id（尾部 `--LAMMPS--ipr1`）。

### 4. OpenKIM 交叉检索（可选）

```bash
python scripts/search_potentials.py --source openkim --elements Cu Ni
```

返回 OpenKIM 模型码与模型页链接。模型文件本体经 `kim-api-collections-management install <MO_...>`
安装（无裸 URL 直链），LAMMPS 内以 `pair_style kim` 调用。

## 回答规范（RSK 证据标签）

- 检索结果属【外部检索】（NIST 镜像/官网/OpenKIM，附 URL）；工具能力说明属【软件知识】
  （`references/` 两份文档 + 本脚本实测）。
- 渲染出的 pair 命令标注来源（NIST 结构化记录渲染 + "运行前核对"）；不可渲染的风格如实说明。
- 未收录某体系时明确说"当前未在 NIST 库检索到该体系的条目"，并给出最近邻候选而非编造。

## 故障排查

| 症状 | 处理 |
|---|---|
| `SSL: UNEXPECTED_EOF_WHILE_READING`（访问 NIST） | 已自动回落 curl；若仍失败检查代理/网络 |
| 索引过期 | `--refresh` 重新下载 CSV；本地缓存 `~/.zcode/cache/nist-potentials/`（env `NIST_POT_CACHE` 可覆盖） |
| 个别条目命令不可渲染 | 属预期（schema 变体/非 eam 族），用官方页面链接人工核对 |
| 回归验证 | `python tests/test_search.py` |

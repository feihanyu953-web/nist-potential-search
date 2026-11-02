#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
search_potentials.py — 按元素 / 模型文件检索 NIST Interatomic Potentials Repository 的 LAMMPS 势函数。

纯 Python 标准库实现（urllib/csv/json/ast/hashlib），无第三方依赖。

数据源（全部为公开资源，2026-11 实测验证，详见 skill 的 references/nist_repo_api.md）：
  1. lmhale99/potentials-library —— NIST Interatomic Potentials Repository 全库 GitHub 镜像
     （NIST 人员 Laura Hale 维护，与 potentials.nist.gov 同源）：
       Potential.csv                全库 951 条元数据索引（id / elements / fictionalelements）
       potential_LAMMPS.csv         716 条 LAMMPS 实现索引（pair_style / units / symbols / artifacts）
       Potential/potential.<id>.json        势条目记录（引用、DOI、bibtex、implementation）
       potential_LAMMPS/<impl>.json         LAMMPS 实现记录（结构化 pair_style / pair_coeff）
  2. NIST 参数文件直链：https://www.ctcms.nist.gov/potentials/Download/<entry-id>/<n>/<filename>
  3. OpenKIM REST（可选 --source openkim）：POST https://query.openkim.org/api/get_available_models

已知坑（勿复踩）：
  - NIST 官网没有单一 JSON 总索引（repository-index.json → 404），检索必须走本镜像 CSV。
  - potentials.nist.gov 裸 HTTPS GET 会超时（需官方 potentials 包的 pymongo 路径），勿依赖。
  - CSV 的 citations/implementations 列是 Python repr（含 datetime.date(...)），不可 json.loads；
    检索只需要 elements/fictionalelements 列，用 ast.literal_eval。
  - OpenKIM query 参数必须是双引号 JSON 字符串、form-encoded POST；database 只能是 obj/data/job/log/agent。

用法：
  python search_potentials.py --input in.lammps            # 从模型文件提取元素并检索
  python search_potentials.py --elements Cu Ni --exact     # 显式元素，体系完全一致
  python search_potentials.py --entry 1985--Foiles-S-M--Ni-Cu --bibtex
  python search_potentials.py --entry 1985--Foiles-S-M--Ni-Cu--LAMMPS--ipr1 --download ./pot
  python search_potentials.py --source openkim --elements Cu Ni
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

LIB_RAW = "https://raw.githubusercontent.com/lmhale99/potentials-library/master"
CSV_POTENTIAL = "Potential.csv"
CSV_LAMMPS = "potential_LAMMPS.csv"
JSON_POTENTIAL = "Potential/potential.{id}.json"
JSON_LAMMPS = "potential_LAMMPS/{id}.json"

NIST_ENTRY_PAGE = "https://www.ctcms.nist.gov/potentials/entry/{entry_id}/"
NIST_DOWNLOAD = "https://www.ctcms.nist.gov/potentials/Download"

OPENKIM_QUERY = "https://query.openkim.org/api/get_available_models"
OPENKIM_MODEL_PAGE = "https://openkim.org/id/"

USER_AGENT = "nist-potential-search-skill/1.0 (+local research tool)"
HTTP_TIMEOUT = 30

# 允许的元素符号（用于从 pair_coeff/data 等自由文本中甄别元素 token）
ELEMENT_RE = re.compile(r"^[A-Z][a-z]?$")

# eam 族：可确定性渲染 pair_coeff 的 pair_style
EAM_FAMILY_AUTO = {"eam", "eam/alloy", "eam/fs", "adp"}
# 这些风格结构多样，只给文件清单 + 官方页面链接，标记"需人工核对"
EAM_FAMILY_MANUAL = {"meam", "meam/c", "meam/spline", "tersoff", "tersoff/zbl",
                     "tersoff/mod", "sw", "reax", "reax/c", "bop", "vashishta",
                     "comb", "comb3", "quip", "hybrid", "hybrid/overlay", "mlip",
                     "snap", "ace", "edip", "extep", "gw", "kolmogorov-crespi-z",
                     "ilp-graphene-hbn", "lcbop", "nb3d", "stillinger-weber"}

ML_STYLES_RE = re.compile(r"deepmd|mace|snap|ace|nequip|allegro|nnp|mlip|pacemaker", re.I)


# ---------------------------------------------------------------------------
# HTTP 与缓存
# ---------------------------------------------------------------------------

def cache_dir() -> str:
    d = os.environ.get("NIST_POT_CACHE") or os.path.join(
        os.path.expanduser("~"), ".zcode", "cache", "nist-potentials")
    os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(d, "json"), exist_ok=True)
    return d


def http_get(url: str, timeout: int = HTTP_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for _ in range(2):  # 简单重试一次
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, OSError) as e:
            last_err = e
            time.sleep(1.0)
    # TLS 兼容性兜底：部分 Python/OpenSSL 组合与 www.ctcms.nist.gov 握手失败
    # （实测 SSL: UNEXPECTED_EOF_WHILE_READING），系统 curl 可正常访问。
    import subprocess
    try:
        r = subprocess.run(["curl", "-sSfL", "--max-time", str(timeout), url],
                           capture_output=True, check=True)
        return r.stdout
    except FileNotFoundError:
        raise RuntimeError(f"HTTP 请求失败且系统无 curl 兜底: {url}\n  {last_err}")
    except subprocess.CalledProcessError as e2:
        raise RuntimeError(f"HTTP 请求失败（urllib 与 curl 均失败）: {url}\n"
                           f"  urllib: {last_err}\n  curl: {e2.stderr.decode('utf-8', 'replace')}")


def _url_cache_path(url: str) -> str:
    h = hashlib.md5(url.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir(), "json", h + ".json")


def fetch_cached(url: str, use_cache: bool = True, refresh: bool = False):
    """GET 并缓存 JSON；缓存命中且不 refresh 时直接读盘。"""
    cp = _url_cache_path(url)
    if use_cache and not refresh and os.path.exists(cp):
        with open(cp, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    data = http_get(url)
    with open(cp, "wb") as f:
        f.write(data)
    return json.loads(data.decode("utf-8"))


def ensure_csv(name: str, refresh: bool = False) -> str:
    """把索引 CSV 下载到本地缓存（若缺失或 refresh）。返回本地路径。"""
    path = os.path.join(cache_dir(), name)
    if not os.path.exists(path) or refresh:
        url = f"{LIB_RAW}/{name}"
        print(f"[cache] 下载 {name} ...", file=sys.stderr)
        data = http_get(url)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return path


def fetch_entry_json(entry_id: str, refresh: bool = False) -> dict:
    url = f"{LIB_RAW}/{JSON_POTENTIAL.format(id=entry_id)}"
    return fetch_cached(url, refresh=refresh)


def fetch_lammps_json(impl_id: str, refresh: bool = False) -> dict:
    url = f"{LIB_RAW}/{JSON_LAMMPS.format(id=impl_id)}"
    return fetch_cached(url, refresh=refresh)


# ---------------------------------------------------------------------------
# CSV 索引
# ---------------------------------------------------------------------------

def literal_list(s: str) -> list:
    """CSV 里 Python repr 风格的列表列 → list。"""
    if not s:
        return []
    try:
        v = ast.literal_eval(s)
        return list(v) if isinstance(v, (list, tuple)) else []
    except (ValueError, SyntaxError):
        return []


def load_lammps_index() -> list:
    path = ensure_csv(CSV_LAMMPS)
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_potential_index() -> dict:
    """Potential.csv → {entry_id: row}，用于 fictional 过滤与全库兜底检索。"""
    path = ensure_csv(CSV_POTENTIAL)
    with open(path, encoding="utf-8") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


# ---------------------------------------------------------------------------
# 元素提取（模型文件 → 元素集合）
# ---------------------------------------------------------------------------

def _norm_symbol(tok: str):
    """'cu'/'CU' → 'Cu'；不是元素样 token 返回 None。"""
    tok = tok.strip().strip('"\'')
    if ELEMENT_RE.match(tok):
        return tok
    if tok.isalpha() and 1 < len(tok) <= 3 and tok[0].isalpha():
        cand = tok.capitalize()
        if ELEMENT_RE.match(cand):
            return cand
    return None


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].rstrip()


def _join_continuations(lines):
    out, buf = [], ""
    for raw in lines:
        s = _strip_comment(raw.rstrip("\n"))
        if buf:
            s = buf + s
        if s.rstrip().endswith("&"):
            buf = s.rstrip()[:-1]
            continue
        buf = ""
        out.append(s)
    return out


def parse_lammps_data(path: str) -> list:
    """LAMMPS data 文件：读 Masses 段的元素符号列。"""
    elements, in_masses = [], False
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = _strip_comment(raw).strip()
            if not line:
                continue
            if in_masses:
                toks = line.split()
                sym = None
                for t in toks[1:]:  # 跳过 type id，找元素符号
                    if _norm_symbol(t):
                        sym = _norm_symbol(t)
                        break
                if sym:
                    elements.append(sym)
            elif re.match(r"^Masses\b", line, re.I):
                in_masses = True
    if not elements:
        raise SystemExit(
            f"无法从 data 文件 {path} 的 Masses 段识别元素符号（该文件可能只写质量不写符号）。\n"
            "请用 --elements 显式指定元素。")
    return sorted(set(elements))


def _lammps_vars(lines) -> dict:
    vars_ = {}
    for line in lines:
        m = re.match(r"^variable\s+(\S+)\s+(string|index|equal)\s+(.+)$", line.strip())
        if m and m.group(2) in ("string", "index"):
            vars_[m.group(1)] = m.group(3).split()[0].strip('"\'')
    return vars_


def parse_lammps_input(path: str) -> list:
    """LAMMPS 输入脚本：优先 pair_coeff 元素列表；否则跟随 read_data 读 data 文件。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = _join_continuations(f.readlines())
    vars_ = _lammps_vars(lines)

    def resolve(tok: str):
        return re.sub(r"\$\{(\w+)\}", lambda m: vars_.get(m.group(1), m.group(0)), tok)

    pair_coeff_elements, data_file = [], None
    for line in lines:
        s = line.strip()
        low = s.lower()
        if low.startswith("pair_coeff"):
            toks = s.split()
            # 形如 pair_coeff * * file El1 El2 ... —— 收集排在文件名后的元素 token
            star_count = sum(1 for t in toks if t == "*")
            rest = toks[1 + star_count:]
            if rest:
                # rest[0] 视为文件名，其余为元素/参数
                for t in rest[1:]:
                    sym = _norm_symbol(t)
                    if sym:
                        pair_coeff_elements.append(sym)
            if rest and pair_coeff_elements:
                break
        elif low.startswith("read_data"):
            data_file = resolve(s.split()[1]) if len(s.split()) > 1 else None

    if pair_coeff_elements:
        return sorted(set(pair_coeff_elements))
    if data_file and "${" not in data_file:
        dpath = data_file if os.path.isabs(data_file) else os.path.join(os.path.dirname(path), data_file)
        if os.path.exists(dpath):
            return parse_lammps_data(dpath)
        raise SystemExit(f"read_data 指向的文件不存在: {dpath}\n请用 --elements 显式指定元素。")
    raise SystemExit(
        f"无法从 LAMMPS 输入 {path} 识别元素（pair_coeff 中无元素列表且没有可解析的 read_data）。\n"
        "input_build 模板的 pair_style/pair_coeff 是注释占位属正常情况，请用 --elements 显式指定。")


def parse_poscar(path: str) -> list:
    """VASP POSCAR：VASP5 格式第 6 行（0-based 5）为元素行。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = [ln.rstrip("\n") for ln in f]
    idx = 5
    if idx >= len(lines):
        raise SystemExit(f"POSCAR 文件行数不足: {path}")
    toks = lines[idx].split()
    # 跳过 Selective dynamics 行
    if toks and toks[0].lower() in ("s", "sel", "selective"):
        idx += 1
        toks = lines[idx].split()
    syms = [_norm_symbol(t) for t in toks]
    if syms and all(syms):
        return sorted(set(syms))
    raise SystemExit(
        f"POSCAR {path} 是 VASP4 格式（元素行是数字，需 POTCAR 才能确定元素）。\n"
        "请用 --elements 显式指定元素。")


def parse_cif(path: str) -> list:
    """CIF：读 _atom_site_type_symbol 列，剥离电荷后缀（Fe3+ → Fe）。"""
    elements = []
    sym_idx = None        # type_symbol 列索引；None = 当前不在 atom_site loop 数据区
    header_count = 0
    in_header = False
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("loop_"):
                in_header, sym_idx, header_count = True, None, 0
                continue
            if in_header and line.startswith("_"):
                tag = line.split()[0].lower()
                if tag == "_atom_site_type_symbol":
                    sym_idx = header_count
                header_count += 1
                continue
            if in_header:
                in_header = False  # 第一条数据行，落到底部按数据处理
            if sym_idx is not None:
                toks = line.split()
                if len(toks) > sym_idx:
                    m = re.match(r"^([A-Z][a-z]?)", toks[sym_idx])
                    if m:
                        elements.append(m.group(1))
    if not elements:
        raise SystemExit(f"无法从 CIF {path} 读取 _atom_site_type_symbol。\n请用 --elements 显式指定。")
    return sorted(set(elements))


def parse_extxyz(path: str) -> list:
    """extxyz / xyz：第 1 行原子数，第 2 行注释，其后各列首 token 为 species。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        first = f.readline().strip()
        f.readline()  # 注释行
        try:
            natoms = int(first.split()[0])
        except (ValueError, IndexError):
            raise SystemExit(f"无法解析 extxyz 文件头: {path}")
        elements = []
        for _ in range(natoms):
            toks = f.readline().split()
            if toks:
                m = re.match(r"^([A-Z][a-z]?)", toks[0])
                if m:
                    elements.append(m.group(1))
    if not elements:
        raise SystemExit(f"无法从 {path} 读取 species 列。\n请用 --elements 显式指定。")
    return sorted(set(elements))


def extract_elements(path: str) -> list:
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path).lower()
    if ext in (".in", ".lammps", ".lmp") or name.startswith("in."):
        return parse_lammps_input(path)
    if ext in (".data", ".lmpdata") or name.startswith("data."):
        return parse_lammps_data(path)
    if ext in (".poscar",) or name.lower().startswith("poscar"):
        return parse_poscar(path)
    if ext == ".cif":
        return parse_cif(path)
    if ext in (".xyz", ".extxyz"):
        return parse_extxyz(path)
    # 内容嗅探兜底
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.read(2048)
    if re.search(r"^\s*(units|pair_style|read_data)\b", head, re.M | re.I):
        return parse_lammps_input(path)
    if re.search(r"^\s*Masses\b", head, re.M | re.I):
        return parse_lammps_data(path)
    raise SystemExit(
        f"无法识别文件类型: {path}（支持 .in/.lammps/.lmp、.data、POSCAR、.cif、.xyz/.extxyz）。\n"
        "请改用 --elements 显式指定元素。")


# ---------------------------------------------------------------------------
# NIST 检索
# ---------------------------------------------------------------------------

def search_lammps_index(query: list, exact: bool, include_fictional: bool,
                        limit: int) -> list:
    """
    在 potential_LAMMPS.csv 里按元素过滤。
    返回 [{potid, impls:[row...], elements:[...]}] 按精确匹配优先、年份降序。
    """
    lmp_rows = load_lammps_index()
    pot_index = load_potential_index()
    qset = set(query)
    candidates = {}
    for row in lmp_rows:
        row_el = set(literal_list(row.get("elements")) ) or set(literal_list(row.get("symbols")))
        if not row_el:
            continue
        pot = pot_index.get(row["potid"], {})
        fictional = set(literal_list(pot.get("fictionalelements")))
        if fictional and not include_fictional:
            continue
        if qset - row_el:
            continue
        if exact and row_el != qset:
            continue
        cand = candidates.setdefault(row["potid"], {"potid": row["potid"],
                                                    "elements": sorted(row_el),
                                                    "impls": []})
        cand["impls"].append(row)
    # 排序：元素集合完全一致者在前；同组按条目年份降序
    def sort_key(c):
        year = int(c["potid"][:4]) if c["potid"][:4].isdigit() else 0
        return (0 if set(c["elements"]) == qset else 1, -year)
    cands = sorted(candidates.values(), key=sort_key)
    if cands:
        return cands[:limit]

    # 零命中回退：展示与查询共享元素最多的条目（最多 10 个）
    scored = []
    for row in lmp_rows:
        row_el = set(literal_list(row.get("elements"))) or set(literal_list(row.get("symbols")))
        if not row_el:
            continue
        pot = pot_index.get(row["potid"], {})
        if set(literal_list(pot.get("fictionalelements"))) and not include_fictional:
            continue
        inter = row_el & qset
        if not inter:
            continue
        hit = [c for c in scored if c["potid"] == row["potid"]]
        if hit:
            hit[0]["impls"].append(row)
            continue
        scored.append({"potid": row["potid"], "elements": sorted(row_el),
                       "impls": [row], "_overlap": len(inter)})
    for c in scored:
        c.pop("_overlap", None)
    scored.sort(key=lambda c: (-len(set(c["elements"]) & qset),
                               -int(c["potid"][:4]) if c["potid"][:4].isdigit() else 0))
    return scored[:10]


def citation_from_entry(entry: dict) -> dict:
    desc = (entry.get("interatomic-potential", entry).get("description") or {})
    cit = desc.get("citation") or {}
    authors = [a.get("surname", "") for a in (cit.get("author") or []) if isinstance(a, dict)]
    pd = cit.get("publication-date")
    year = pd.get("year") if isinstance(pd, dict) else pd
    return {
        "title": cit.get("title"),
        "authors": authors,
        "year": year,
        "journal": cit.get("publication-name"),
        "doi": cit.get("DOI"),
        "bibtex": cit.get("bibtex"),
    }


def kim_links_from_entry(entry: dict) -> list:
    impls = entry.get("interatomic-potential", entry).get("implementation") or []
    links = []
    for imp in impls:
        if not isinstance(imp, dict):
            continue  # 个别条目 implementation 里混有字符串项
        if imp.get("type") == "OpenKIM":
            wl = ((imp.get("link") or {}).get("web-link") or {})
            if isinstance(wl, dict) and wl.get("URL"):
                links.append(wl["URL"])
    return links


def _normalize_pair_coeff(pc) -> list:
    """
    归一化 potential-LAMMPS 记录的 pair_coeff 字段（schema 有多种变体）→
    [{"files":[...], "symbols":[...] or None, "symbols_flag": bool}]
    已知变体：
      A. list[{interaction:{symbol:[...]}, term:{file:...}}]          (eam funcfl 多文件)
      B. dict{term:[{file:...},{symbols:true}]}                       (setfl 单行 + 元素标志)
      C. dict{term:{file:...}} / list[{term:{file:...}}]
    """
    items = pc if isinstance(pc, list) else [pc]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        term = it.get("term")
        terms = term if isinstance(term, list) else ([term] if term else [])
        files = [t.get("file") for t in terms if isinstance(t, dict) and t.get("file")]
        symbols_flag = any(isinstance(t, dict) and t.get("symbols") for t in terms)
        inter = it.get("interaction") or {}
        syms = inter.get("symbol") if isinstance(inter, dict) else None
        out.append({"files": files, "symbols": syms or None, "symbols_flag": bool(symbols_flag)})
    return out


def render_pair_commands(lmp_record: dict, row_symbols=None):
    """
    从 potential-LAMMPS 结构化记录渲染 pair_style / pair_coeff 命令。
    返回 (pair_style_line, [pair_coeff_lines], manual_flag, rec)。
    渲染规则（对应 LAMMPS 文档与 input_build 约定）：
      eam(funcfl) 单文件      -> pair_coeff * * file          （无元素）
      eam(funcfl) 多文件      -> 每行 pair_coeff * * file El
      eam/alloy|eam/fs|adp   -> pair_coeff * * file El1 El2 ...
      meam/c                 -> pair_coeff * * library.meam El... param.meam El...
      其余风格               -> manual_flag=True，仅输出文件与元素清单
    """
    rec = lmp_record.get("potential-LAMMPS", lmp_record)
    ps = rec.get("pair_style") or {}
    if isinstance(ps, str):
        style, ps_extra = ps, {}
    elif isinstance(ps, dict):
        style = ps.get("type", "")
        ps_extra = {k: v for k, v in ps.items() if k != "type"}
    else:
        style, ps_extra = "", {}
    pair_style_line = "pair_style " + style
    if ps_extra:
        pair_style_line += " " + " ".join(str(v) for v in ps_extra.values())

    syms_row = [s for s in (row_symbols or []) if isinstance(s, str)]
    items = _normalize_pair_coeff(rec.get("pair_coeff"))
    coeff_lines = []
    manual = style in EAM_FAMILY_MANUAL

    if style == "eam":
        files_items = [it for it in items if it["files"]]
        if len(files_items) == 1 and len(files_items[0]["files"]) == 1:
            coeff_lines.append(f"pair_coeff * * {files_items[0]['files'][0]}")
        else:
            for it in files_items:
                f = it["files"][0]
                el = (it["symbols"] or [None])[0] or (syms_row[0] if syms_row else None)
                if el is None:
                    manual = True
                    break
                coeff_lines.append(f"pair_coeff * * {f} {el}")
    elif style in ("eam/alloy", "eam/fs", "adp"):
        all_files = sorted({f for it in items for f in it["files"]})
        syms = None
        for it in items:
            if it["symbols"]:
                syms = list(dict.fromkeys(it["symbols"]))
                break
            if it["symbols_flag"] and syms_row:
                syms = syms_row
                break
        if syms is None and syms_row:
            syms = syms_row
        if all_files and syms:
            for f in all_files:
                coeff_lines.append("pair_coeff * * {} {}".format(f, " ".join(syms)))
        else:
            manual = True
    elif style in ("meam", "meam/c", "meam/spline") and items:
        # 标准形态：library.meam + <el>.meam；LAMMPS 文档：pair_coeff * * library.meam El... file.meam El...
        files = [f for it in items for f in it["files"]]
        syms = None
        for it in items:
            if it["symbols"]:
                syms = list(dict.fromkeys(it["symbols"]))
                break
            if it["symbols_flag"] and syms_row:
                syms = syms_row
                break
        if len(files) == 2 and syms:
            coeff_lines.append("pair_coeff * * {} {} {} {}".format(
                files[0], " ".join(syms), files[1], " ".join(syms)))
            coeff_lines.append("# 注: 现代 LAMMPS 推荐 pair_style meam/c；以上为记录原样风格，运行前请核对")
        else:
            manual = True

    return pair_style_line, coeff_lines, manual, rec


def print_search_results(query, cands, detail_n: int, show_bibtex: bool):
    if not cands:
        print("未检索到匹配的 LAMMPS 势函数条目。")
        print("可尝试：去掉 --exact；检查元素拼写；或加 --include-fictional。")
        return
    print(f"\n查询元素: {' '.join(query)}    命中 {len(cands)} 个条目（前 {min(len(cands), 10)} 见下表）\n")
    full = [c for c in cands if set(query) <= set(c["elements"])]
    if not full:
        print("> 无包含全部查询元素的条目——以下为**共享元素最多的最近邻候选**（供分体系/单元素参考；"
              "多元素复杂体系建议分别按金属子体系检索，或考虑 ML 势）。\n")
    print("| # | 条目 (entry id) | pair_style | 体系元素 |")
    print("|---|---|---|---|")
    for i, c in enumerate(cands, 1):
        styles = sorted({r["pair_style"] for r in c["impls"]})
        print(f"| {i} | {c['potid']} | {', '.join(styles)} | {' '.join(c['elements'])} |")

    print(f"\n=== 详细信息（前 {detail_n} 条）===\n")
    for c in cands[:detail_n]:
        try:
            entry = fetch_entry_json(c["potid"])
            cit = citation_from_entry(entry)
            kim = kim_links_from_entry(entry)
        except Exception as e:
            cit, kim = {"title": None, "year": None, "doi": None, "bibtex": None, "authors": []}, []
            print(f"[warn] 拉取条目记录失败 {c['potid']}: {e}", file=sys.stderr)
        year = cit.get("year") or c["potid"][:4]
        auth = ", ".join(cit.get("authors", [])[:3]) + (" et al." if len(cit.get("authors", [])) > 3 else "")
        print(f"### {c['potid']}")
        if cit.get("title"):
            print(f"- 引用: {auth} ({year}) {cit['title']}, {cit.get('journal')}, DOI: https://doi.org/{cit['doi']}" if cit.get("doi")
                  else f"- 引用: {auth} ({year}) {cit['title']}, {cit.get('journal')}")
        print(f"- 官方页面: {NIST_ENTRY_PAGE.format(entry_id=c['potid'])}")
        for k in kim:
            print(f"- OpenKIM: {k}")
        for row in c["impls"]:
            impl_id = row["id"]
            try:
                lmp_rec = fetch_lammps_json(impl_id)
                ps_line, coeffs, manual, rec = render_pair_commands(
                    lmp_rec, row_symbols=literal_list(row.get("symbols")))
                units = rec.get("units", "?")
                atom_style = rec.get("atom_style", "?")
            except Exception as e:
                print(f"  - 实现 {impl_id}: 记录拉取失败 ({e})")
                continue
            print(f"  - 实现 {impl_id}  [{row['pair_style']}, units={units}, atom_style={atom_style}]")
            if coeffs:
                print("    ```")
                print(f"    {ps_line}")
                for cl in coeffs:
                    print(f"    {cl}")
                print("    ```")
            else:
                print(f"    pair_style: {row['pair_style']}（结构化命令不可自动渲染，需人工核对）")
            arts = literal_list(row.get("artifacts"))
            for a in arts:
                if isinstance(a, dict) and a.get("url"):
                    print(f"    文件: {a['filename']}  →  {a['url']}")
            if manual:
                print(f"    ⚠ 该 pair_style 需人工核对命令写法，见官方页面 {NIST_ENTRY_PAGE.format(entry_id=c['potid'])}")
        if show_bibtex and cit.get("bibtex"):
            print("  - bibtex:")
            for bl in cit["bibtex"].strip().splitlines():
                print(f"      {bl}")
        print()


# ---------------------------------------------------------------------------
# 单条目详情 / 下载
# ---------------------------------------------------------------------------

def resolve_entry(impl_or_entry: str):
    """--entry 两种口径：势条目 id 或 LAMMPS 实现 id。返回 (potid, [impl_rows])。"""
    lmp_rows = load_lammps_index()
    if impl_or_entry.endswith(".json"):
        impl_or_entry = impl_or_entry[:-5]
    rows = [r for r in lmp_rows if r["id"] == impl_or_entry]
    if rows:
        return rows[0]["potid"], rows
    rows = [r for r in lmp_rows if r["potid"] == impl_or_entry]
    if rows:
        return impl_or_entry, rows
    # 允许省略 --LAMMPS--ipr1 尾巴的直接条目 id（即使没有 LAMMPS 实现，也能给出条目引用信息）
    pot_index = load_potential_index()
    if impl_or_entry in pot_index:
        return impl_or_entry, []
    raise SystemExit(f"--entry {impl_or_entry} 不在索引中（既非 LAMMPS 实现 id，也非势条目 id）。")


def print_entry_detail(potid: str, rows: list, show_bibtex: bool):
    try:
        entry = fetch_entry_json(potid)
        cit = citation_from_entry(entry)
        kim = kim_links_from_entry(entry)
    except Exception as e:
        print(f"[warn] 拉取条目记录失败: {e}", file=sys.stderr)
        cit, kim = {"title": None, "year": None, "doi": None, "bibtex": None, "authors": []}, []
    auth = ", ".join(cit.get("authors", [])[:3]) + (" et al." if len(cit.get("authors", [])) > 3 else "")
    print(f"### {potid}")
    if cit.get("title"):
        print(f"- 引用: {auth} ({cit.get('year')}) {cit['title']}, {cit.get('journal')}"
              + (f", DOI: https://doi.org/{cit['doi']}" if cit.get("doi") else ""))
    print(f"- 官方页面: {NIST_ENTRY_PAGE.format(entry_id=potid)}")
    for k in kim:
        print(f"- OpenKIM: {k}")
    if show_bibtex and cit.get("bibtex"):
        print("- bibtex:")
        for bl in cit["bibtex"].strip().splitlines():
            print(f"    {bl}")
    if not rows:
        print("- 该条目没有收录 LAMMPS 实现（可能仅有 OpenKIM/ASE 等实现）。")
        return
    for row in rows:
        impl_id = row["id"]
        try:
            lmp_rec = fetch_lammps_json(impl_id)
            ps_line, coeffs, manual, rec = render_pair_commands(
                lmp_rec, row_symbols=literal_list(row.get("symbols")))
        except Exception as e:
            print(f"- 实现 {impl_id}: 记录拉取失败 ({e})")
            continue
        print(f"- 实现 {impl_id}  [{row['pair_style']}, units={rec.get('units','?')}, atom_style={rec.get('atom_style','?')}]")
        if coeffs:
            print("  ```")
            print(f"  {ps_line}")
            for cl in coeffs:
                print(f"  {cl}")
            print("  ```")
        else:
            print(f"  pair_style: {row['pair_style']}（需人工核对命令写法）")
        for a in literal_list(row.get("artifacts")):
            if isinstance(a, dict) and a.get("url"):
                print(f"  文件: {a['filename']}  →  {a['url']}")
        if manual:
            print(f"  ⚠ 需人工核对命令写法，见 {NIST_ENTRY_PAGE.format(entry_id=potid)}")


def download_entry(potid: str, rows: list, outdir: str) -> None:
    """下载参数文件到 outdir，并写 manifest（文件名/sha256/来源/条目/DOI）。"""
    os.makedirs(outdir, exist_ok=True)
    try:
        entry = fetch_entry_json(potid)
        cit = citation_from_entry(entry)
    except Exception:
        cit = {"doi": None, "title": None, "year": None}
    manifest_path = os.path.join(outdir, "nist_manifest.md")
    lines = [f"# NIST 势函数下载清单 — {datetime.now().isoformat(timespec='seconds')}",
             f"- 条目: {potid}",
             f"- 官方页面: {NIST_ENTRY_PAGE.format(entry_id=potid)}"]
    if cit.get("doi"):
        lines.append(f"- DOI: https://doi.org/{cit['doi']}")
    if cit.get("title"):
        lines.append(f"- 文献: {cit['title']} ({cit.get('year')})")
    lines.append("")
    lines.append("| 文件 | sha256 | 来源 URL |")
    lines.append("|---|---|---|")

    n = 0
    for row in rows:
        impl_id = row["id"]
        try:
            rec = fetch_lammps_json(impl_id).get("potential-LAMMPS", {})
        except Exception as e:
            print(f"[warn] {impl_id} 记录拉取失败: {e}", file=sys.stderr)
            continue
        artifacts = rec.get("artifact")
        if artifacts is None:
            artifacts = literal_list(row.get("artifacts"))
            items = [{"filename": a.get("filename"), "web-link": {"URL": a.get("url")}}
                     for a in artifacts if isinstance(a, dict)]
        elif isinstance(artifacts, dict):
            items = [artifacts]
        else:
            items = artifacts
        for a in items:
            wl = a.get("web-link") or {}
            url, fname = wl.get("URL"), a.get("filename") or (wl.get("URL", "").rsplit("/", 1)[-1])
            if not url:
                continue
            dest = os.path.join(outdir, fname)
            if not os.path.exists(dest):
                data = http_get(url)
                with open(dest, "wb") as f:
                    f.write(data)
            sha = hashlib.sha256(open(dest, "rb").read()).hexdigest()
            print(f"[dl] {fname}  ({os.path.getsize(dest)} bytes)  sha256={sha[:16]}...")
            lines.append(f"| {fname} | {sha} | {url} |")
            n += 1
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n共下载 {n} 个文件 → {outdir}\n清单: {manifest_path}")
    print("提醒：pair_style/pair_coeff 须经你确认后手动写入输入文件（input_build 的 never-invent-potentials 规则）。")


# ---------------------------------------------------------------------------
# OpenKIM（可选交叉源）
# ---------------------------------------------------------------------------

def openkim_search(query: list, model_interface: str, limit: int, exact: bool) -> None:
    params = {
        "species": json.dumps(query),
        # OpenKIM 只允许 species_logic = ["and"] 或 ["exact"]（无 "or"）
        "species_logic": json.dumps(["exact"] if exact else ["and"]),
        "model_interface": json.dumps([model_interface]),
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(OPENKIM_QUERY, data=data, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            models = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise SystemExit(f"OpenKIM 查询失败: {e}\n注意参数必须是双引号 JSON、form-encoded POST。")
    if not isinstance(models, list):
        print(f"OpenKIM 返回异常: {models}")
        return
    print(f"\nOpenKIM 可用模型（species={' '.join(query)}, logic={'exact' if exact else 'and'}, interface={model_interface}）: {len(models)} 个\n")
    for m in models[:limit]:
        if isinstance(m, dict):
            code = m.get("kimcode") or m.get("name") or str(m)
        else:
            code = str(m)
        print(f"- {code}\n  https://openkim.org/id/{code}")
    if len(models) > limit:
        print(f"... 其余 {len(models) - limit} 个已省略（--limit 可调）")
    print("\n注：OpenKIM 模型文件本体通过 kim-api 安装（kim-api-collections-management install <MO_...>），非裸 URL 下载。")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="按元素/模型文件检索 NIST Interatomic Potentials Repository 的 LAMMPS 势函数",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("用法：")[-1])
    ap.add_argument("--input", help="模型文件：LAMMPS .in/.data、POSCAR、.cif、.xyz/.extxyz")
    ap.add_argument("--elements", nargs="+", help="显式元素列表，如 --elements Cu Ni")
    ap.add_argument("--entry", help="势条目 id 或 LAMMPS 实现 id（详情/下载模式）")
    ap.add_argument("--exact", action="store_true", help="只保留体系元素完全一致的条目（默认：包含全部查询元素即可）")
    ap.add_argument("--include-fictional", action="store_true", help="包含含虚构/粗粒化元素的条目（默认排除）")
    ap.add_argument("--source", choices=["nist", "openkim"], default="nist")
    ap.add_argument("--kim-model-interface", default="any", help="OpenKIM model_interface 过滤（默认 any）")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--detail", type=int, default=5, help="详情展开的条目数")
    ap.add_argument("--download", metavar="DIR", help="下载 --entry 的参数文件到 DIR")
    ap.add_argument("--bibtex", action="store_true", help="输出 bibtex")
    ap.add_argument("--refresh", action="store_true", help="强制更新本地索引缓存")
    args = ap.parse_args(argv)

    # ---- 详情 / 下载模式 ----
    if args.entry:
        potid, rows = resolve_entry(args.entry)
        if args.download:
            download_entry(potid, rows, args.download)
        else:
            print_entry_detail(potid, rows, args.bibtex)
        return

    # ---- 检索模式 ----
    if args.input:
        query = extract_elements(args.input)
        print(f"从 {args.input} 提取到元素: {' '.join(query)}", file=sys.stderr)
        if ML_STYLES_RE.search(open(args.input, encoding="utf-8", errors="replace").read() if os.path.getsize(args.input) < 1e6 else ""):
            print("[提示] 该输入使用 ML 势（deepmd/mace 等）；NIST 库以经典势为主，以下为同体系经典势候选，可作对照/基准。", file=sys.stderr)
    elif args.elements:
        query = []
        for e in args.elements:
            s = _norm_symbol(e)
            if not s:
                raise SystemExit(f"无效元素符号: {e}")
            query.append(s)
    else:
        ap.error("需要 --input 或 --elements（或 --entry 详情模式）")

    if args.source == "openkim":
        openkim_search(query, args.kim_model_interface, args.limit, args.exact)
    else:
        cands = search_lammps_index(query, args.exact, args.include_fictional, args.limit)
        print_search_results(query, cands, args.detail, args.bibtex)


if __name__ == "__main__":
    main()

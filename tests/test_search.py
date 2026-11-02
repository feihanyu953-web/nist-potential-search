#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_search.py — nist-potential-search 回归测试。

运行：
    python tests/test_search.py

需联网。覆盖：
  1. 索引缓存（Potential.csv / potential_LAMMPS.csv 可下载加载）
  2. 元素提取（LAMMPS .in→read_data、POSCAR、.data Masses、CIF、extxyz）
  3. 检索（Cu 单元素命中 Foiles 1986；--exact 语义）
  4. 命令渲染（funcfl 无元素 / eam-alloy 带元素）
  5. 条目引用（DOI）
  6. NIST 直链可达性（curl 兜底路径）
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import search_potentials as sp  # noqa: E402

PASS, FAIL = 0, []


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    print("== 1. 索引缓存 ==")
    p1 = sp.ensure_csv(sp.CSV_POTENTIAL)
    p2 = sp.ensure_csv(sp.CSV_LAMMPS)
    check("Potential.csv 存在", os.path.exists(p1))
    check("potential_LAMMPS.csv 存在", os.path.exists(p2))
    lmp_rows = sp.load_lammps_index()
    check("LAMMPS 索引 >=700 条", len(lmp_rows) >= 700, f"实际 {len(lmp_rows)}")
    pot_index = sp.load_potential_index()
    check("全库索引 >=900 条", len(pot_index) >= 900, f"实际 {len(pot_index)}")

    print("== 2. 元素提取 ==")
    tmp = tempfile.mkdtemp(prefix="nps_test_")
    inp = os.path.join(tmp, "in.lammps")
    dat = os.path.join(tmp, "data.test")
    write(inp, "units metal\nread_data data.test\npair_style deepmd m.pb\npair_coeff * *\n")
    write(dat, "LAMMPS data\n\nMasses\n\n1 1.008 H\n2 14.007 N\n3 15.999 O\n4 132.905 Cs\n5 195.084 Pt\n")
    check("LAMMPS .in → read_data", sp.extract_elements(inp) == ["Cs", "H", "N", "O", "Pt"],
          str(sp.extract_elements(inp)))
    poscar = os.path.join(tmp, "POSCAR")
    write(poscar, "t\n1.0\n3.6 0 0\n0 3.6 0\n0 0 3.6\nCu Ni\n1 1\nDirect\n0 0 0\n.5 .5 .5\n")
    check("POSCAR VASP5", sp.extract_elements(poscar) == ["Cu", "Ni"])
    check("data Masses 直读", sp.parse_lammps_data(dat) == ["Cs", "H", "N", "O", "Pt"])
    cif = os.path.join(tmp, "a.cif")
    write(cif, "data_a\nloop_\n_atom_site_label\n_atom_site_type_symbol\n_atom_site_fract_x\n"
               "Fe1 Fe3+ 0.0\nO1 O2- 0.5\n")
    check("CIF 电荷剥离", sp.extract_elements(cif) == ["Fe", "O"], str(sp.extract_elements(cif)))
    xyz = os.path.join(tmp, "m.extxyz")
    write(xyz, "2\nLattice=\"3.0 0 0 0 3.0 0 0 0 3.0\" Properties=species:S:1:pos:R:3\n"
               "Mo 0 0 0\nW 1.5 1.5 1.5\n")
    check("extxyz", sp.extract_elements(xyz) == ["Mo", "W"])

    print("== 3. 检索 ==")
    cands = sp.search_lammps_index(["Cu"], exact=False, include_fictional=False, limit=100)
    ids = {c["potid"] for c in cands}
    check("Cu 命中 Foiles 1986",
          "1986--Foiles-S-M-Baskes-M-I-Daw-M-S--Cu" in ids, f"命中 {len(ids)} 条")
    cands_exact = sp.search_lammps_index(["Cu", "Ni"], exact=True, include_fictional=False, limit=100)
    check("--exact 只含 Cu-Ni 完全体",
          all(set(c["elements"]) == {"Cu", "Ni"} for c in cands_exact)
          and len(cands_exact) >= 3, f"命中 {len(cands_exact)} 条")
    near = sp.search_lammps_index(["Cs", "H", "N", "O", "Pt"], exact=False,
                                  include_fictional=False, limit=100)
    check("复杂体系零命中 → 最近邻回退",
          all(not ({"Cs", "H", "N", "O", "Pt"} <= set(c["elements"])) for c in near)
          and len(near) > 0, f"回退 {len(near)} 条")

    print("== 4. 命令渲染 ==")
    rec = sp.fetch_lammps_json("1986--Foiles-S-M--Cu--LAMMPS--ipr1")
    ps, coeffs, manual, _ = sp.render_pair_commands(rec)
    check("funcfl 渲染: pair_style eam", ps.strip() == "pair_style eam", ps)
    check("funcfl 渲染: 无元素符号", coeffs == ["pair_coeff * * Cu_u3.eam"], str(coeffs))
    check("funcfl 非 manual", manual is False)
    rec2 = sp.fetch_lammps_json("2019--Fischer-F--Cu-Ni--LAMMPS--ipr1")
    ps2, coeffs2, manual2, _ = sp.render_pair_commands(rec2, row_symbols=["Cu", "Ni"])
    check("eam/alloy 渲染带元素",
          ps2.strip() == "pair_style eam/alloy" and coeffs2 == ["pair_coeff * * Cu_Ni_Fischer_2018.eam.alloy Cu Ni"],
          f"{ps2} / {coeffs2}")
    ps3, coeffs3, manual3, _ = sp.render_pair_commands(
        sp.fetch_lammps_json("2025--Sharifi-H--Cu-Ni--LAMMPS--ipr1"), row_symbols=["Cu", "Ni"])
    check("meam 出模板且 manual",
          manual3 is True and any("library.meam" in c for c in coeffs3), f"{coeffs3}")

    print("== 5. 条目引用 ==")
    entry = sp.fetch_entry_json("1986--Foiles-S-M-Baskes-M-I-Daw-M-S--Cu")
    cit = sp.citation_from_entry(entry)
    check("DOI = 10.1103/physrevb.33.7983", cit.get("doi") == "10.1103/physrevb.33.7983", str(cit.get("doi")))
    check("年份 1986", cit.get("year") == 1986, str(cit.get("year")))
    check("OpenKIM 链接 >=1", len(sp.kim_links_from_entry(entry)) >= 1)

    print("== 6. NIST 直链 ==")
    data = sp.http_get("https://www.ctcms.nist.gov/potentials/Download/1985--Foiles-S-M--Ni-Cu/1/Cu_smf7.eam")
    check("Cu_smf7.eam 可达（36 KB）", 30000 < len(data) < 50000, f"{len(data)} bytes")

    print(f"\n结果: {PASS} pass, {len(FAIL)} fail")
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()

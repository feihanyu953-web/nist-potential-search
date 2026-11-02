# nist-potential-search

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)

An [agent skill](https://specs.zcode.chat/skills) (ZCode / Claude Code-compatible) **and** standalone CLI that searches the [NIST Interatomic Potentials Repository](https://www.ctcms.nist.gov/potentials/) for LAMMPS interatomic potentials matching your system — parsed automatically from a model file (LAMMPS input/data, POSCAR, CIF, extxyz) or from an explicit element list.

It presents ranked candidates with citation, DOI, `pair_style` / `pair_coeff` snippets and direct parameter-file links, downloads the chosen files with a **sha256 manifest** after your confirmation, and optionally cross-searches [OpenKIM](https://openkim.org/).

> **语言提示 / Language note**: the skill instructions, reference docs, and CLI output are written in Chinese (中文). The CLI itself is language-neutral. English docs welcome via PR.

## Why

- NIST's potential repository has **no single JSON index** — searching it means crawling hundreds of per-entry pages. This skill uses the full-repository GitHub mirror [`lmhale99/potentials-library`](https://github.com/lmhale99/potentials-library) (maintained by NIST's Laura Hale) as a structured, searchable index, then links back to official NIST pages and files.
- Picking a potential usually means remembering which entry fits your elements, which `pair_style` it uses, and where the parameter file lives. This skill automates the lookup, renders copy-paste `pair_style`/`pair_coeff` lines for eam/meam families, and checksums every downloaded file.
- It is deliberately conservative: it **never invents potentials** and **never writes to your input files**.

## Features

- **Element extraction from model files**: LAMMPS `.in/.lammps/.lmp` (reads `pair_coeff`, follows `read_data` → Masses), LAMMPS `.data`, VASP5 POSCAR, `.cif`, `.xyz/.extxyz`
- **Search**: exact-composition match or ranked by shared elements; zero-hit systems fall back to nearest-neighbor candidates (e.g. Cs|H|N|O|Pt)
- **Per-entry details**: citation, DOI, bibtex, rendered `pair_style`/`pair_coeff` command blocks, direct download URLs
- **Download with integrity**: fetches parameter files into your target dir and writes a `nist_manifest.md` sha256 manifest
- **OpenKIM cross-search** (`--source openkim`): returns KIM model codes and model pages (install via `kim-api-collections-management`, use with `pair_style kim`)
- **Zero dependencies**: pure Python 3.8+ standard library; automatic curl fallback if urllib TLS handshake to `www.ctcms.nist.gov` fails
- **Offline-friendly caching**: index CSVs (~3.4 MB) cached in `~/.zcode/cache/nist-potentials/`, refresh with `--refresh`

## Install

### As an agent skill (ZCode / Claude Code)

```bash
git clone https://github.com/feihanyu953-web/nist-potential-search.git ~/.zcode/skills/nist-potential-search
```

(For Claude Code, clone into `~/.claude/skills/nist-potential-search` instead.) The skill's frontmatter triggers on requests like "find a potential for Cu-Ni", "which pair_style/file should I use", or during LAMMPS input preparation.

### As a standalone CLI

No install needed — just clone anywhere and run:

```bash
python scripts/search_potentials.py --elements Cu Ni
```

## CLI usage

```bash
# Extract elements from a model file, then search
python scripts/search_potentials.py --input in.lammps

# Explicit element list; --exact = composition must match exactly
python scripts/search_potentials.py --elements Cu Ni --exact

# Entry details + bibtex
python scripts/search_potentials.py --entry 1985--Foiles-S-M--Ni-Cu --bibtex

# Download parameter files (entry id or LAMMPS implementation id), writes sha256 manifest
python scripts/search_potentials.py --entry 1985--Foiles-S-M--Ni-Cu--LAMMPS--ipr1 --download ./potentials/

# OpenKIM cross-search
python scripts/search_potentials.py --source openkim --elements Cu Ni

# Other options: --limit N  --detail N  --include-fictional  --refresh
```

`--entry` accepts either a potential entry id (`1985--Foiles-S-M--Ni-Cu`) or a LAMMPS implementation id (suffix `--LAMMPS--ipr1`).

## Safety design (HARD GATEs)

1. **Ask, don't guess**: if element extraction fails (VASP4 POSCAR, unsigned Masses, fully commented `pair_coeff`...), the script exits with an error instead of guessing.
2. **Confirm before download**: downloads only run when explicitly invoked with `--download`.
3. **Never write input files**: rendered `pair_style`/`pair_coeff` lines are candidates only; integrating them into LAMMPS inputs is the caller's (or a separate input-builder skill's) job. funcfl single files → `pair_style eam`; setfl files → `eam/alloy` / `eam/fs` and require element symbols.

Non-renderable pair styles (tersoff, reax, schema variants...) are flagged for manual review against the official NIST page rather than guessed.

## Repository layout

```
nist-potential-search/
├── SKILL.md                      # agent-skill instructions (frontmatter + workflow)
├── scripts/
│   └── search_potentials.py      # main CLI (stdlib only, ~900 lines)
├── references/
│   ├── nist_repo_api.md          # NIST repository data pipeline & known pitfalls
│   └── openkim_api.md            # OpenKIM REST query notes
└── tests/
    └── test_search.py            # regression tests (needs network)
```

## Testing

```bash
python tests/test_search.py
```

Covers: index download/cache, element extraction (all 5 formats), search semantics, command rendering, citation/DOI, and NIST direct-link reachability (curl fallback).

## Data sources & attribution

- [NIST Interatomic Potentials Repository](https://www.ctcms.nist.gov/potentials/) — potential entries, citations, parameter files
- [`lmhale99/potentials-library`](https://github.com/lmhale99/potentials-library) — structured full-repository mirror (CSV + JSON), maintained by Laura Hale (NIST)
- [OpenKIM](https://openkim.org/) — model registry (optional cross-search)

This project is an independent search tool and is **not affiliated with or endorsed by NIST or OpenKIM**. Always verify a potential against its original publication before production use; rendered `pair_style`/`pair_coeff` lines are convenience output and must be checked before running.

## License

[MIT](LICENSE)

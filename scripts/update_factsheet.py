"""Keep FACT_SHEET.md in sync with the code and record every release.

    python scripts/update_factsheet.py                        # regenerate AUTO blocks
    python scripts/update_factsheet.py --bump patch -m "Fix FEMA layer id"
    python scripts/update_factsheet.py --bump minor -m "Prithvi-WxC hosted"
    python scripts/update_factsheet.py --check                # CI: fail if stale

--bump updates the version in src/ppfe/__init__.py and pyproject.toml, stamps
today's date, and adds a Version-history line. AUTO blocks (version, model
card, AOIs) are rebuilt from the code every run; everything else is hand-edited.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FS = ROOT / "FACT_SHEET.md"
INIT = ROOT / "src" / "ppfe" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"


def get_version() -> str:
    return re.search(r'__version__\s*=\s*"([^"]+)"', INIT.read_text()).group(1)


def set_version(v: str) -> None:
    INIT.write_text(re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{v}"', INIT.read_text()))
    PYPROJECT.write_text(re.sub(r'(?m)^version\s*=\s*"[^"]+"', f'version = "{v}"', PYPROJECT.read_text(), count=1))


def bump(v: str, part: str) -> str:
    ma, mi, pa = (int(x) for x in v.split(".")[:3])
    return {"major": f"{ma + 1}.0.0", "minor": f"{ma}.{mi + 1}.0", "patch": f"{ma}.{mi}.{pa + 1}"}[part]


def model_card():
    from ppfe.models.registry import _resolve

    return _resolve(os.environ.get("PPFE_MODEL", "placeholder")).card


def block(name: str, body: str) -> str:
    return f"<!-- AUTO:{name} -->\n{body.strip()}\n<!-- /AUTO:{name} -->"


def build_version(v: str, day: str) -> str:
    c = model_card()
    return block("version", f"**Version {v}** · updated {day} · model `{c.name}` v{c.version}")


def build_model() -> str:
    c = model_card()
    fmt = lambda vs: ", ".join(f"{x.name} ({x.units})" if x.units else x.name for x in vs)  # noqa: E731
    rows = [
        ("Name / version", f"`{c.name}` v{c.version}"),
        ("Description", c.description),
        ("Downscale factor", f"×{c.scale_factor}"),
        ("Inputs (HRRR)", fmt(c.inputs)),
        ("Outputs", fmt(c.outputs)),
        ("Runtime", "ONNX Runtime (CUDA / DirectML / CoreML / CPU, auto-selected)"),
    ]
    return block("model", "| | |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows))


def build_aois() -> str:
    from ppfe.data.aoi import list_aois

    aois = list_aois(ROOT / "aois")
    rows = "\n".join(f"| {a['region']} | {a['name']} | {', '.join(f'{x:.2f}' for x in a['bbox'])} |" for a in aois)
    note = "\n\n_Informal extents; official AOIs pending._" if any(a["informal"] for a in aois) else ""
    return block("aois", "| Region | AOI | Extent (W, S, E, N) |\n|---|---|---|\n" + rows + note)


def render(text: str, v: str, day: str) -> str:
    for name, body in (("version", build_version(v, day)), ("model", build_model()), ("aois", build_aois())):
        pat = re.compile(rf"<!-- AUTO:{name} -->.*?<!-- /AUTO:{name} -->", re.S)
        if not pat.search(text):
            sys.exit(f"FACT_SHEET.md is missing the AUTO:{name} block")
        text = pat.sub(lambda _m, b=body: b, text)
    return text


def current_day(text: str) -> str:
    m = re.search(r"updated (\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else date.today().isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bump", choices=["major", "minor", "patch"])
    ap.add_argument("-m", "--message", help="what changed (required with --bump)")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    text = FS.read_text(encoding="utf-8")
    v, day = get_version(), current_day(text)

    if a.check:
        if f"- **{v}**" not in text:
            sys.exit(f"FACT_SHEET.md has no Version history entry for {v}. Run with --bump or add one.")
        if render(text, v, day) != text:
            sys.exit("FACT_SHEET.md AUTO blocks are stale. Run: python scripts/update_factsheet.py")
        print(f"FACT_SHEET.md up to date (v{v})")
        return

    if a.bump:
        if not a.message:
            sys.exit("--bump needs -m 'what changed'")
        v, day = bump(v, a.bump), date.today().isoformat()
        set_version(v)
        text = text.replace("<!-- CHANGELOG -->", f"<!-- CHANGELOG -->\n- **{v}** ({day}): {a.message}", 1)

    FS.write_text(render(text, v, day), encoding="utf-8")
    print(f"FACT_SHEET.md -> v{v} ({day})")


if __name__ == "__main__":
    main()

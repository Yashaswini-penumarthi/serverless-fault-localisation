#!/usr/bin/env python
"""Build the Lambda zips for terraform/ without the zip tool or SAM.

    python tools/bundle_lambdas.py

Writes build/{faultlab-layer,orders-api,inventory,payments,notifications}.zip.
The layer holds the faultlab package plus aws-xray-sdk (pinned in
lambdas/layer/layer-deps.txt) installed for python3.12 on arm64.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"


def zip_dir(src: Path, dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                info = zipfile.ZipInfo(str(p.relative_to(src)), date_time=(2026, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())


def main() -> int:
    shutil.rmtree(BUILD, ignore_errors=True)
    stage = BUILD / "layer-stage" / "python"
    stage.mkdir(parents=True)
    shutil.copytree(ROOT / "lambdas/layer/faultlab", stage / "faultlab", ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "lambdas/layer/layer-deps.txt"),
                    "-t", str(stage), "--platform", "manylinux2014_aarch64", "--only-binary=:all:",
                    "--python-version", "3.12", "--implementation", "cp"], check=True)
    zip_dir(BUILD / "layer-stage", BUILD / "faultlab-layer.zip")
    for svc in ("orders_api", "inventory", "payments", "notifications"):
        d = BUILD / f"stage-{svc}"
        d.mkdir()
        shutil.copy(ROOT / "lambdas" / svc / "handler.py", d / "app.py")
        zip_dir(d, BUILD / f"{svc.replace('_', '-')}.zip")
    for z in sorted(BUILD.glob("*.zip")):
        print(z.name, z.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

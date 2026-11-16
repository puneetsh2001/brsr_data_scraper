"""
React frontend (frontend/): unit and component tests (Vitest) and a check
that the build served at /app/ is current. Skipped when Node.js is absent.

The Vitest run uses the resolved project path as working directory: on
Windows, a working directory whose drive letter differs in case (c:\\ vs
C:\\) makes Vitest load two copies of itself.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import PROJECT_ROOT

FRONTEND = PROJECT_ROOT / "frontend"


def find_node_dir():
    exe = shutil.which("node")
    if exe:
        return Path(exe).parent
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
    found = sorted(base.glob("node-v*-win-x64"), reverse=True) if base.is_dir() else []
    return found[0] if found else None


NODE_DIR = find_node_dir()
pytestmark = pytest.mark.skipif(
    NODE_DIR is None or not (FRONTEND / "node_modules").is_dir(),
    reason="Node.js or frontend dependencies not installed")


def test_vitest_suite_passes():
    env = dict(os.environ, PATH=f"{NODE_DIR}{os.pathsep}{os.environ['PATH']}", CI="1")
    npx = NODE_DIR / ("npx.cmd" if os.name == "nt" else "npx")
    r = subprocess.run([str(npx), "vitest", "run"], cwd=str(FRONTEND.resolve()), env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600, shell=os.name == "nt")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-3000:]
    assert "failed" not in out.split("Test Files")[-1].split("\n")[0], out[-2000:]


def test_build_is_current():
    """The build served at /app/ must not be older than its sources."""
    index = FRONTEND / "dist" / "index.html"
    assert index.is_file(), "run `npm run build` in frontend/"
    sources = [p for p in (FRONTEND / "src").rglob("*") if p.is_file() and "test" not in p.parts]
    sources += [FRONTEND / "index.html", FRONTEND / "vite.config.js", FRONTEND / "package.json"]
    newest = max(p.stat().st_mtime for p in sources)
    assert index.stat().st_mtime >= newest, "frontend/dist is older than src/: run `npm run build`"
    html = index.read_text(encoding="utf-8")
    assert "/app/assets/" in html and "config.js" in html


def test_runtime_config_has_no_secrets():
    cfg = (FRONTEND / "public" / "config.js").read_text(encoding="utf-8")
    for word in ("password", "secret", "token", "DB_"):
        assert word.lower() not in cfg.lower()

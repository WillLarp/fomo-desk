"""Acceptance test 7: secret greps and plan B 404 checks."""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "__pycache__", "data", "logs", "screenshots", "node_modules", ".pytest_cache"}
SECRET_NAMES = ("TYPESAFE_API_KEY", "TG_TOKEN", "DATA_PATH")


def _iter_text_files():
    for p in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix in {".py", ".js", ".html", ".css", ".md", ".sh", ".json", ".txt", ".yml", ".yaml"}:
            # skip this test file's mentions of the env names
            yield p


def test_no_secret_values_committed():
    values = []
    for name in SECRET_NAMES:
        val = os.environ.get(name)
        if val and len(val) >= 8:
            values.append((name, val))
    try:
        from paths import load_secrets, data_path
        dp = None
        try:
            dp = data_path()
        except Exception:
            dp = load_secrets().get("DATA_PATH")
        if dp:
            values.append(("DATA_PATH", dp))
    except Exception:
        pass
    leaked = []
    for p in _iter_text_files():
        if p.name in {"test_hygiene.py"}:
            continue
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        for name, val in values:
            if val and val in text:
                leaked.append(f"{p.relative_to(ROOT)} contains {name}")
    assert leaked == [], leaked


def test_web_does_not_build_fomo_urls():
    """Front-end must use coinUrl/profile_url from JSON, not construct fomo.family links."""
    bad = []
    for p in (ROOT / "web").rglob("*"):
        if not p.is_file() or p.suffix not in {".js", ".html"}:
            continue
        text = p.read_text(errors="ignore")
        if "fomo.family" in text:
            bad.append(str(p.relative_to(ROOT)))
    assert bad == [], f"FOMO URL built outside links.py: {bad}"


def test_plan_b_has_no_index_under_d():
    web_d = ROOT / "web" / "d"
    assert not (web_d / "index.html").exists()
    # publish tree may exist locally; it must not have an index in d/ or in the secret folder
    pub = ROOT / "data" / "publish" / "d"
    if pub.exists():
        assert not (pub / "index.html").exists()
        for child in pub.iterdir():
            if child.is_dir():
                assert not (child / "index.html").exists()


def test_404_page_exists():
    assert (ROOT / "web" / "404.html").is_file()
    body = (ROOT / "web" / "404.html").read_text()
    assert "Not found" in body

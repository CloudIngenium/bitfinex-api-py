"""Packaging floors and the CI interpreter matrix."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def test_requests_floor_excludes_cve_2026_25645():
    # GHSA-gc5v-m9x4-r6x2 / CVE-2026-25645 (insecure temp file reuse in
    # extract_zipped_paths) is fixed in requests 2.33.0.
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "dependencies"
    ]
    (spec,) = [d for d in deps if re.match(r"requests\b", d)]
    floor = re.search(r">=\s*([0-9.]+)", spec)
    assert floor, spec
    assert _version(floor.group(1)) >= (2, 33, 0)


def test_ci_tests_every_supported_python():
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
    for version in ("3.12", "3.13", "3.14"):
        assert f'"{version}"' in workflow, version
    assert "${{ matrix.python-version }}" in workflow

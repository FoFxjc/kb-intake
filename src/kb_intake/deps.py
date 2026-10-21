"""Installed versions of everything that shapes the output (intake-design §3.2, §9.3.1, §9.3.3).

`uv tool install` does not read uv.lock, so `constraints.txt` (generated from uv.lock, shipped with the package, kept
in sync by CI) is what pins the transitive dependencies at install time. At run time this module:
- computes the dependency closure of docling and pymupdf from the installed metadata (torch, transformers, pillow,
  numpy, torchvision, ... follow from the metadata, nobody lists them by hand);
- compares the installed version of each with constraints.txt. A mismatch makes the engine "not ready": formal
  packaging is refused unless the caller forces it, and the manifest records the forced run.
The closure with its versions is also part of the cache key.
"""

import importlib.metadata as md
import re
from functools import cache
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOTS = ("docling", "pymupdf")


def constraints_file() -> Path:
    here = Path(__file__).resolve().parent
    for p in (here / "constraints.txt", here.parents[1] / "constraints.txt"):     # wheel, then source checkout
        if p.is_file():
            return p
    raise FileNotFoundError("constraints.txt not found")


@cache
def expected() -> dict[str, str]:
    """name -> version from constraints.txt, for this platform (environment markers evaluated)."""
    env = default_environment()
    out = {}
    lines = constraints_file().read_text(encoding="utf-8").splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        req = Requirement(line)
        if req.marker is not None and not req.marker.evaluate(env):
            continue
        pinned = next((s.version for s in req.specifier if s.operator == "=="), None)
        if pinned:
            out[canonicalize_name(req.name)] = pinned
    return out


@cache
def closure() -> dict[str, str]:
    """name -> installed version for ROOTS and everything they require on this platform, following the extras a
    requirement asks for (docling pulls torch and transformers in through `docling-slim[...]`)."""
    env = default_environment()
    seen: dict[str, str] = {}
    done: set[tuple[str, str]] = set()
    todo = [(r, "") for r in ROOTS]
    while todo:
        raw, extra = todo.pop()
        name = canonicalize_name(raw)
        if (name, extra) in done:
            continue
        done.add((name, extra))
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            seen[name] = "missing"
            continue
        seen[name] = dist.version
        for spec in dist.requires or []:
            req = Requirement(spec)
            if req.marker is None or req.marker.evaluate({**env, "extra": extra}):
                todo += [(req.name, "")] + [(req.name, e) for e in sorted(req.extras)]
    return dict(sorted(seen.items()))


def _public(v: str) -> str:
    try:
        return Version(v).public           # "2.14.1+cpu" (torch's CPU wheel on Linux) is "2.14.1"
    except Exception:  # noqa: BLE001
        return re.sub(r"\+.*", "", v)


def problems() -> list[str]:
    """Installed packages of the closure whose version differs from constraints.txt (empty = ready)."""
    want = expected()
    out = []
    for name, have in closure().items():
        if have == "missing":
            out.append(f"{name} is not installed")
        elif name not in want:
            out.append(f"{name} {have} is not in constraints.txt")
        elif _public(have) != want[name]:
            out.append(f"{name} {have} installed, constraints.txt pins {want[name]}")
    return out

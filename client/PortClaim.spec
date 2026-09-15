# -*- mode: python ; coding: utf-8 -*-
"""Compatibility entry point for the canonical Windows packaging spec."""
from pathlib import Path
PORTCLAIM_ROOT = Path(SPECPATH).resolve().parent
_spec = PORTCLAIM_ROOT / "deploy/windows/PortClaim.spec"
exec(compile(_spec.read_text(encoding="utf-8"), str(_spec), "exec"))

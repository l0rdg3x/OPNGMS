from __future__ import annotations

import io
import tarfile
from pathlib import Path

import httpx

_CODELOAD = "https://codeload.github.com/opnsense/{repo}/tar.gz/refs/tags/{ref}"


def extract_tarball(data: bytes, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        # filter="data" (Python 3.12+) refuses absolute paths, `..` traversal, unsafe symlinks and
        # device nodes in one pass — no manual TOCTOU guard needed.
        tf.extractall(dest, filter="data")
    return dest


def fetch_source(repo: str, ref: str, dest: Path, *, timeout: float = 60.0) -> Path:
    """Download opnsense/<repo> at tag <ref> and extract to dest. Network: CLI use only."""
    url = _CODELOAD.format(repo=repo, ref=ref)
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return extract_tarball(resp.content, dest)

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import httpx

_CODELOAD = "https://codeload.github.com/opnsense/{repo}/tar.gz/refs/tags/{ref}"


def extract_tarball(data: bytes, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        # Path-traversal guard: refuse any member that escapes dest.
        for member in tf.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise ValueError(f"unsafe tar member: {member.name}")
        tf.extractall(dest)
    return dest


def fetch_source(repo: str, ref: str, dest: Path, *, timeout: float = 60.0) -> Path:
    """Download opnsense/<repo> at tag <ref> and extract to dest. Network: CLI use only."""
    url = _CODELOAD.format(repo=repo, ref=ref)
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return extract_tarball(resp.content, dest)

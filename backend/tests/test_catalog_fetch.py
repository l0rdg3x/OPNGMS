import io
import tarfile

import pytest

from tools.opnsense_catalog.fetch import extract_tarball


def _targz(name: str, data: bytes = b"") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extract_tarball_unpacks_to_dest(tmp_path):
    dest = tmp_path / "out"
    root = extract_tarball(_targz("core-26.1.8/src/x/IDS.xml", b"<model/>"), dest)
    assert (root / "core-26.1.8/src/x/IDS.xml").read_bytes() == b"<model/>"


def test_extract_tarball_rejects_path_traversal(tmp_path):
    # filter="data" must refuse a `..`-escaping member rather than write outside dest.
    with pytest.raises((ValueError, tarfile.TarError, OSError)):
        extract_tarball(_targz("../escape.txt", b"x"), tmp_path / "out")

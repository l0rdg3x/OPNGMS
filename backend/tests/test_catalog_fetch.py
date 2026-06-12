import io
import tarfile

from tools.opnsense_catalog.fetch import extract_tarball


def test_extract_tarball_unpacks_to_dest(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"<model/>"
        info = tarfile.TarInfo("core-26.1.8/src/x/IDS.xml")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    dest = tmp_path / "out"
    root = extract_tarball(buf.getvalue(), dest)
    assert (root / "core-26.1.8/src/x/IDS.xml").read_bytes() == b"<model/>"

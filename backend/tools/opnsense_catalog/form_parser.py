from __future__ import annotations

from defusedxml import ElementTree as DET


def parse_forms(named_xmls: list[tuple[str, str]]) -> dict[str, dict]:
    """named_xmls: [(form_name, xml_text)]. Returns {field_id: {label, help, page}}."""
    out: dict[str, dict] = {}
    for page, xml_text in named_xmls:
        try:
            root = DET.fromstring(xml_text)
        except Exception:  # noqa: BLE001 - a malformed form must not abort the whole model
            continue
        for fld in root.iter("field"):
            fid = (fld.findtext("id") or "").strip()
            if not fid:
                continue
            out[fid] = {
                "label": (fld.findtext("label") or "").strip(),
                "help": (fld.findtext("help") or "").strip(),
                "page": page,
            }
    return out

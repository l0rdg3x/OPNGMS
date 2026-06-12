from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelSource:
    module: str
    model_xml: str
    form_paths: list[Path] = field(default_factory=list)
    controller_paths: list[Path] = field(default_factory=list)


def discover_models(root: Path) -> list[ModelSource]:
    out: list[ModelSource] = []
    for model_xml in sorted(root.rglob("mvc/app/models/OPNsense/*/*.xml")):
        module = model_xml.parent.name
        app = model_xml.parents[3]                      # .../mvc/app
        forms_dir = app / "views/OPNsense" / module / "forms"
        ctrls_dir = app / "controllers/OPNsense" / module / "Api"
        forms = sorted(forms_dir.glob("*.xml")) if forms_dir.is_dir() else []
        ctrls = sorted(ctrls_dir.glob("*.php")) if ctrls_dir.is_dir() else []
        out.append(ModelSource(module=module, model_xml=str(model_xml),
                               form_paths=forms, controller_paths=ctrls))
    return out

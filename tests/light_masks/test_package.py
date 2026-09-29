import hashlib
import json
from zipfile import ZipFile

from scripts import package_light_masks


def test_package_excludes_private_records(tmp_path, monkeypatch):
    component = tmp_path / "custom_components" / "light_masks"
    component.mkdir(parents=True)
    (component / "manifest.json").write_text(json.dumps({"version": "0.0.0"}), encoding="utf-8")
    (component / "__init__.py").write_text('"""Test integration."""\n', encoding="utf-8")
    (component / "__pycache__").mkdir()
    (component / "__pycache__" / "test.pyc").write_bytes(b"cache")
    for name in ("README.md", "LICENSE"):
        (tmp_path / name).write_text("Public documentation\n", encoding="utf-8")
    for name in (
        "docs/verification.md",
        "researches/light_masks_integration_specification.md",
        ".storage/private.json",
        ".vs/private.txt",
    ):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("PRIVATE_FIXTURE_DO_NOT_SHIP", encoding="utf-8")
    monkeypatch.setattr(package_light_masks, "ROOT", tmp_path)
    monkeypatch.setattr(package_light_masks, "COMPONENT", component)

    package_light_masks.main()

    archive_path = tmp_path / "dist" / "light-masks-0.0.0.zip"
    with ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {
            "custom_components/light_masks/manifest.json",
            "custom_components/light_masks/__init__.py",
            "README.md",
            "LICENSE",
        }
        assert all(b"PRIVATE_FIXTURE" not in archive.read(name) for name in archive.namelist())
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert archive_path.with_suffix(".zip.sha256").read_text(encoding="ascii") == (
        f"{digest}  {archive_path.name}\n"
    )

"""Build the manual-install ZIP without bundling dependencies, caches or tests."""

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "light_masks"


def main() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    files = [
        path
        for path in COMPONENT.rglob("*")
        if path.is_file() and path.suffix in {".py", ".json", ".yaml", ".png"}
    ]
    files.extend(
        [
            ROOT / "README.md",
            ROOT / "researches" / "light_masks_integration_specification.md",
            ROOT / "docs" / "verification.md",
            ROOT / "LICENSE",
        ]
    )
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    archive_path = destination / f"light-masks-{manifest['version']}.zip"
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files):
            entry = ZipInfo(path.relative_to(ROOT).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, path.read_bytes())
    with ZipFile(archive_path) as archive:
        if bad_file := archive.testzip():
            raise RuntimeError(f"Archive integrity failure: {bad_file}")
        if set(archive.namelist()) != {path.relative_to(ROOT).as_posix() for path in files}:
            raise RuntimeError("Archive contents differ from the selected files")
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix(".zip.sha256").write_text(
        f"{digest}  {archive_path.name}\n", encoding="ascii"
    )
    print(f"{archive_path} ({len(files)} files, {archive_path.stat().st_size} bytes)")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()

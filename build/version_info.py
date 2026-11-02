"""Read the project release identity and encode Windows executable metadata."""
from __future__ import annotations

from pathlib import Path

from packaging.version import Version


def project_version(root: Path) -> str:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    with (root / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def make_windows_version_info(current_version: str):
    parsed = Version(current_version)
    if parsed.epoch or len(parsed.release) > 4 or any(part > 0xFFFF for part in parsed.release):
        raise ValueError(f"Cannot encode Windows release version: {current_version}")
    numeric = parsed.release + (0,) * (4 - len(parsed.release))
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
    )
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numeric, prodvers=numeric, mask=0x3F,
                          flags=0x02 if parsed.is_prerelease else 0x00,
                          OS=0x40004, fileType=0x01, subtype=0x00, date=(0, 0)),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("FileDescription", "Holocron Toolset - KotOR resource editor"),
            StringStruct("FileVersion", current_version),
            StringStruct("InternalName", "HolocronToolset"),
            StringStruct("OriginalFilename", "HolocronToolset.exe"),
            StringStruct("ProductName", "Holocron Toolset"),
            StringStruct("ProductVersion", current_version),
        ])]), VarFileInfo([VarStruct("Translation", [0x0409, 1200])])],
    )

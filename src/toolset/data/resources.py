"""Physical resource inventory for editor suggestions, not game lookup precedence."""
from __future__ import annotations

import os
import stat
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

from pykotor.extract.capsule import Capsule
from pykotor.resource.type import ResourceType
from pykotor.tools.misc import is_capsule_file

if TYPE_CHECKING:
    from pykotor.extract.file import FileResource
    from toolset.data.installation import HTInstallation

Progress = Callable[[int | str, str], None]


@dataclass(frozen=True)
class ResourceLocation:
    """One physical record. Duplicate keys intentionally remain separate records."""

    name: str
    restype: ResourceType
    path: Path
    offset: int
    size: int
    record: int
    file_size: int
    mtime_ns: int

    @property
    def encapsulated(self) -> bool:
        return (self.offset, self.size) != (0, self.file_size)

    @property
    def filename(self) -> str:
        return f"{self.name}.{self.restype.extension}" if self.encapsulated else self.path.name

    @property
    def display(self) -> str:
        if self.encapsulated:
            return f"{self.path} :: {self.filename} [record {self.record}, offset {self.offset}]"
        return str(self.path)

    def read(self, start: int = 0, length: int | None = None) -> bytes:
        """Read this exact record, never resolve its name to a different duplicate."""
        if self.restype == ResourceType.SAV or self.path.suffix.lower() == ".sav":
            raise ValueError("SAV resources are not supported.")
        if start < 0 or start > self.size or (length is not None and length < 0):
            raise ValueError("Invalid resource byte range.")
        count = self.size - start if length is None else min(length, self.size - start)
        with self.path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Not a regular file: {self.path}")
            if (info.st_size, info.st_mtime_ns) != (self.file_size, self.mtime_ns):
                raise ValueError(f"The source changed. Refresh the resource list: {self.path}")
            if self.offset < 0 or self.size < 0 or self.offset + self.size > info.st_size:
                raise ValueError(f"Resource range is outside its source file: {self.display}")
            stream.seek(self.offset + start)
            data = stream.read(count)
            if len(data) != count:
                raise ValueError(f"Source changed while reading: {self.path}")
            after = os.fstat(stream.fileno())
            if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                raise ValueError(f"Source changed while reading: {self.path}")
            return data

    def read_for_editing(self) -> bytes:
        if self.encapsulated and is_capsule_file(self.filename):
            raise ValueError("Editing archives nested within other containers is not supported.")
        if self.encapsulated and is_capsule_file(self.path.name):
            first = Capsule(self.path).info(self.name, self.restype)
            if first is None or (first.offset(), first.size()) != (self.offset, self.size):
                raise ValueError(f"{self.display}: this is a hidden duplicate or its source changed. Refresh the list, or inspect it read-only through Show resource locations. A name-based editor cannot safely save this physical occurrence.")
        return self.read()


def locations_from_resources(resources: Iterable[FileResource], report: Progress | None = None) -> tuple[ResourceLocation, ...]:
    resources = tuple(resources)
    if report is not None:
        report(len(resources), "set_maximum")
        report("Indexing physical resource locations", "update_maintask_text")
    stats = {}
    records = defaultdict(int)
    result = []
    for index, resource in enumerate(resources):
        path = Path(str(resource.filepath())).absolute()
        if resource.restype() == ResourceType.SAV or path.suffix.lower() == ".sav":
            continue
        if path not in stats:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Not a regular resource source: {path}")
            stats[path] = (info.st_size, info.st_mtime_ns)
        ordinal = records[path]
        records[path] += 1
        container = is_capsule_file(path.name) or path.suffix.lower() == ".bif"
        offset = resource.offset() if container else 0
        size = resource.size() if container else stats[path][0]
        result.append(ResourceLocation(resource.resname(), resource.restype(), path,
                                       offset, size, ordinal, *stats[path]))
        if report is not None and (index % 512 == 511 or index == len(resources) - 1):
            report(index + 1, "set_value")
    return tuple(result)


class ResourceCatalog:
    """Immutable metadata snapshot. Its order is not resource-resolution order."""

    def __init__(self, locations: Iterable[ResourceLocation] = ()):
        self.locations = tuple(locations)
        by_key = defaultdict(list)
        names = defaultdict(set)
        for location in self.locations:
            by_key[location.name.casefold(), location.restype].append(location)
            if location.restype not in (ResourceType.INVALID, ResourceType.SAV):
                names[location.restype].add(location.name)
        self._by_key = {key: tuple(value) for key, value in by_key.items()}
        self._names = {key: frozenset(value) for key, value in names.items()}

    @classmethod
    def from_installation(cls, installation: HTInstallation, report: Progress | None = None, *, reload: bool = False) -> ResourceCatalog:
        if reload:
            from pykotor.extract.installation import Installation
            installation = Installation(installation.path(), progress_callback=report)
        # __iter__ includes voice/music, all indexed modules, overrides and packs.
        # core_resources additionally supplies patch.erf, absent from that iterator.
        resources = tuple(installation)
        seen = {id(resource) for resource in resources}
        resources += tuple(resource for resource in installation.core_resources() if id(resource) not in seen)
        return cls(locations_from_resources(resources, report))

    def names(self, restypes: Iterable[ResourceType]) -> list[str]:
        names = set()
        for restype in restypes:
            names.update(self._names.get(restype, ()))
        return sorted(names, key=lambda text: (text.casefold(), text))

    def find(self, name: str, restypes: Iterable[ResourceType]) -> tuple[ResourceLocation, ...]:
        return tuple(location for restype in restypes for location in self._by_key.get((name.casefold(), restype), ()))

    def with_context(self, filepath: str | os.PathLike | None, report: Progress | None = None) -> ResourceCatalog:
        """Include the explicitly open archive or loose file's immediate directory."""
        if filepath is None:
            return self
        path = Path(str(filepath)).absolute()
        if not path.exists():
            return self  # Unsaved document: no physical source has been created.
        if path.suffix.lower() == ".sav":
            raise ValueError("SAV resources are not supported.")
        if is_capsule_file(path.name):
            added = locations_from_resources(Capsule(path), report)
            retained = (location for location in self.locations if location.path != path)
        elif path.suffix.lower() == ".bif":
            return self  # BIF keys belong to the installation's chitin index.
        else:
            from pykotor.extract.file import FileResource
            directory = path if path.is_dir() else path.parent
            paths = sorted((entry for entry in directory.iterdir() if entry.is_file() and entry.suffix.lower() != ".sav"), key=lambda entry: entry.name)
            added = locations_from_resources((FileResource.from_path(entry) for entry in paths), report)
            included = {location.path for location in added}
            retained = (location for location in self.locations if location.path not in included)
        return ResourceCatalog((*retained, *added))


def inspect_path(path: str | os.PathLike, catalog: ResourceCatalog | None = None, report: Progress | None = None) -> tuple[ResourceLocation, ...]:
    """List a selected file/container without reading or editing resource payloads."""
    from pykotor.extract.file import FileResource

    path = Path(path).absolute()
    if path.suffix.lower() == ".sav":
        raise ValueError("SAV resources are not supported.")
    if is_capsule_file(path.name):
        return locations_from_resources(Capsule(path), report)
    if path.suffix.lower() == ".bif":
        matches = () if catalog is None else tuple(row for row in catalog.locations if row.path == path)
        if not matches:
            raise ValueError("Select the installation whose chitin.key indexes this BIF. BIF resources are identified by that index.")
        return matches
    return locations_from_resources((FileResource.from_path(path),), report)

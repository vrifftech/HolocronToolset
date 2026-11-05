"""Textures available to a model preview and the tables that associate them with it."""
from __future__ import annotations

from collections import defaultdict
from functools import cached_property
from typing import TYPE_CHECKING

from pykotor.common.misc import Game
from pykotor.extract.file import FileResource, ResourceIdentifier
from pykotor.extract.installation import SearchLocation, TexturePackNames
from pykotor.resource.formats.twoda import read_2da
from pykotor.resource.formats.txi import read_txi
from pykotor.resource.generics.uti import read_uti
from pykotor.resource.type import ResourceType
from pykotor.tools.path import CaseAwarePath

if TYPE_CHECKING:
    from pykotor.extract.capsule import Capsule
    from pykotor.extract.installation import Installation


class ModelTextureCatalog:
    def __init__(self, installation: Installation | None, capsules: list[Capsule],
                 folders: list[CaseAwarePath], model_names: set[str], requested_textures: set[str], game: Game):
        self.model_names = {name.lower() for name in model_names if name}
        self.requested_textures = requested_textures
        source_groups = []
        types = (ResourceType.TPC, ResourceType.TGA, ResourceType.TXI, ResourceType.TwoDA, ResourceType.UTI)
        for folder in folders:
            paths = sorted(folder.safe_rglob("*"), key=lambda path: (len(path.parts), str(path).casefold()))
            source_groups.append([
                FileResource.from_path(path) for path in paths
                if ResourceType.from_extension(path.suffix) in types and path.safe_isfile()
            ])
        if installation is not None:
            source_groups.extend(installation.override_resources(name) for name in installation.override_list())
        source_groups.extend(list(capsule) for capsule in capsules)
        core = [] if installation is None else installation.core_resources()
        self.resources = {}
        for group in [*source_groups, core]:
            for resource in group:
                if resource.restype() in (ResourceType.TwoDA, ResourceType.UTI):
                    self.resources.setdefault(resource.identifier(), resource)
        if installation is not None:
            locations = installation.locations(
                set(self.resources),
                [SearchLocation.CUSTOM_FOLDERS, SearchLocation.OVERRIDE, SearchLocation.CUSTOM_MODULES, SearchLocation.CHITIN],
                capsules=capsules, folders=folders,
            )
            self.resources = {identifier: matches[0].as_file_resource()
                              for identifier, matches in locations.items() if matches}

        packs = []
        if installation is not None:
            filenames = {name.lower(): name for name in installation.texturepacks_list()}
            for pack in (TexturePackNames.TPA, TexturePackNames.GUI):
                if pack.value in filenames:
                    packs.append(installation.texturepack_resources(filenames[pack.value]))
        self.textures: dict[str, FileResource] = {}
        sidecars = {}
        # Match the preview's source order, including TPC before TGA within a source.
        for group in [*source_groups, *packs, core]:
            for restype in (ResourceType.TPC, ResourceType.TGA, ResourceType.TXI):
                for resource in group:
                    if resource.restype() == restype:
                        name = resource.resname().lower()
                        target = sidecars if restype == ResourceType.TXI else self.textures
                        target.setdefault(name, resource)
        game = installation.game() if installation is not None else game
        for name, resource in sidecars.items():
            if name not in self.textures:
                settings = read_txi(resource.data()).settings(game=game)
                if settings.controller is not None or settings.texture["filerange"] > 0:
                    self.textures[name] = resource

    def _table(self, name):
        resource = self.resources.get(ResourceIdentifier(name, ResourceType.TwoDA))
        return None if resource is None else read_2da(resource.data())

    @cached_property
    def suggestions(self) -> dict[str, set[str]]:
        reasons = defaultdict(set)

        def suggest(name, reason):
            name = name.strip().lower()
            if name in self.textures:
                reasons[name].add(reason)

        for name in self.requested_textures:
            suggest(name, "Requested by a mesh in this model")

        appearance = self._table("appearance")
        bodies = defaultdict(set)
        if appearance is not None:
            headers = appearance.get_headers()
            model_columns = [h for h in headers if h.startswith("model") and len(h) == 6]
            for index in range(appearance.get_height()):
                row = appearance.get_row(index)
                if row.get_string("modeltype").upper() != "B":
                    if row.get_string("race").lower() in self.model_names:
                        suggest(row.get_string("racetex"), f"appearance.2da row {index}: racetex")
                    continue
                for column in model_columns:
                    model = row.get_string(column).strip()
                    variation = column[-1]
                    for texture_column in (f"tex{variation}", f"tex{variation}evil"):
                        if texture_column not in headers:
                            continue
                        prefix = row.get_string(texture_column).strip().lower()
                        if prefix in ("", "****"):
                            continue
                        bodies[variation].add((model, prefix, texture_column))
                        if model.lower() in self.model_names:
                            reason = f"appearance.2da row {index}: {column}={model}, {texture_column}={prefix}"
                            for name in self.textures:
                                if name.startswith(prefix) and name[len(prefix):].isdigit():
                                    suggest(name, reason)

        heads = self._table("heads")
        if heads is not None:
            columns = [h for h in heads.get_headers() if h.startswith("headtex") or h == "alttexture"]
            for index in range(heads.get_height()):
                row = heads.get_row(index)
                if row.get_string("head").lower() in self.model_names:
                    for column in columns:
                        suggest(row.get_string(column), f"heads.2da row {index}: {column}")

        baseitems = self._table("baseitems")
        if baseitems is not None:
            related = set()
            for index in range(baseitems.get_height()):
                row = baseitems.get_row(index)
                stem = row.get_string("itemclass").lower()
                stems = {stem}
                if row.get_integer("genderspecific"):
                    stems.update((stem + "_m", stem + "_f"))
                for name in self.model_names:
                    family, separator, variant = name.rpartition("_")
                    if (separator and variant.isdigit() and family in stems) or row.get_string("defaultmodel").lower() == name:
                        related.add(index)
            if related:
                for identifier, resource in self.resources.items():
                    if identifier.restype != ResourceType.UTI:
                        continue
                    item = read_uti(resource.data())
                    if item.base_item not in related:
                        continue
                    row = baseitems.get_row(item.base_item)
                    variant = f"{item.model_variation:03}"
                    model = row.get_string("defaultmodel").replace("001", variant)
                    suggest(model, f"Texture named for item model {model}: {resource.resname()}.uti, baseitems.2da row {item.base_item}")
                    body = row.get_string("bodyvar").lower()
                    for model, prefix, column in bodies[body]:
                        suggest(prefix + f"{item.texture_variation:02}",
                                f"Related worn model {model}: {resource.resname()}.uti TextureVar={item.texture_variation}; "
                                f"baseitems.2da row {item.base_item} bodyvar={body.upper()}; appearance.2da {column}={prefix}")
        return dict(reasons)

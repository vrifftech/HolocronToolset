from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, ClassVar

from pykotor.common.misc import CaseInsensitiveDict
from pykotor.extract.file import ResourceIdentifier, ResourceResult
from pykotor.extract.installation import Installation, SearchLocation
from pykotor.resource.formats.tpc import TPC, TPCTextureFormat
from pykotor.resource.formats.twoda import TwoDA, read_2da
from pykotor.resource.type import ResourceType
from PyQt5.QtGui import QImage, QPixmap, QStandardItemModel, QTransform
from utility.error_handling import assert_with_variable_trace, format_exception_with_variables

if TYPE_CHECKING:
    from pykotor.resource.generics.uti import UTI
    from PyQt5.QtWidgets import QWidget


class HTInstallation(Installation):
    TwoDA_PORTRAITS: ClassVar[str] = "portraits"
    TwoDA_APPEARANCES: ClassVar[str] = "appearance"
    TwoDA_SUBRACES: ClassVar[str] = "subrace"
    TwoDA_SPEEDS: ClassVar[str] = "creaturespeed"
    TwoDA_SOUNDSETS: ClassVar[str] = "soundset"
    TwoDA_FACTIONS: ClassVar[str] = "repute"
    TwoDA_GENDERS: ClassVar[str] = "gender"
    TwoDA_PERCEPTIONS: ClassVar[str] = "ranges"
    TwoDA_CLASSES: ClassVar[str] = "classes"
    TwoDA_FEATS: ClassVar[str] = "feat"
    TwoDA_POWERS: ClassVar[str] = "spells"
    TwoDA_BASEITEMS: ClassVar[str] = "baseitems"
    TwoDA_PLACEABLES: ClassVar[str] = "placeables"
    TwoDA_DOORS: ClassVar[str] = "genericdoors"
    TwoDA_CURSORS: ClassVar[str] = "cursors"
    TwoDA_TRAPS: ClassVar[str] = "traps"
    TwoDA_RACES: ClassVar[str] = "racialtypes"
    TwoDA_SKILLS: ClassVar[str] = "skills"
    TwoDA_UPGRADES: ClassVar[str] = "upgrade"
    TwoDA_ENC_DIFFICULTIES: ClassVar[str] = "encdifficulty"
    TwoDA_ITEM_PROPERTIES: ClassVar[str] = "itempropdef"
    TwoDA_IPRP_PARAMTABLE: ClassVar[str] = "iprp_paramtable"
    TwoDA_IPRP_COSTTABLE: ClassVar[str] = "iprp_costtable"
    TwoDA_IPRP_ABILITIES: ClassVar[str] = "iprp_abilities"
    TwoDA_IPRP_ALIGNGRP: ClassVar[str] = "iprp_aligngrp"
    TwoDA_IPRP_COMBATDAM: ClassVar[str] = "iprp_combatdam"
    TwoDA_IPRP_DAMAGETYPE: ClassVar[str] = "iprp_damagetype"
    TwoDA_IPRP_PROTECTION: ClassVar[str] = "iprp_protection"
    TwoDA_IPRP_ACMODTYPE: ClassVar[str] = "iprp_acmodtype"
    TwoDA_IPRP_IMMUNITY: ClassVar[str] = "iprp_immunity"
    TwoDA_IPRP_SAVEELEMENT: ClassVar[str] = "iprp_saveelement"
    TwoDA_IPRP_SAVINGTHROW: ClassVar[str] = "iprp_savingthrow"
    TwoDA_IPRP_ONHIT: ClassVar[str] = "iprp_onhit"
    TwoDA_IPRP_AMMOTYPE: ClassVar[str] = "iprp_ammotype"
    TwoDA_IPRP_MONSTERHIT: ClassVar[str] = "iprp_mosterhit"
    TwoDA_IPRP_WALK: ClassVar[str] = "iprp_walk"
    TwoDA_EMOTIONS: ClassVar[str] = "emotion"
    TwoDA_EXPRESSIONS: ClassVar[str] = "facialanim"
    TwoDA_VIDEO_EFFECTS: ClassVar[str] = "videoeffects"
    TwoDA_DIALOG_ANIMS: ClassVar[str] = "dialoganimations"
    TwoDA_PLANETS: ClassVar[str] = "planetary"
    TwoDA_PLOT: ClassVar[str] = "plot"
    TwoDA_CAMERAS: ClassVar[str] = "camerastyle"

    def __init__(self, path: str, name: str, tsl: bool, mainWindow: QWidget | None, *, progress_callback=None):
        super().__init__(path, progress_callback=progress_callback)

        self.resource_catalog = None

        self.name: str = name
        self.tsl: bool = tsl

        self.mainWindow: QWidget = mainWindow
        self.cacheCoreItems: QStandardItemModel | None = None

        self._cache2da: CaseInsensitiveDict[TwoDA] = CaseInsensitiveDict()
        self._cacheTpc: CaseInsensitiveDict[TPC] = CaseInsensitiveDict()

    # region Cache 2DA
    def htGetCache2DA(self, resname: str) -> TwoDA:
        """Gets a 2DA resource from the cache or loads it if not present.

        Args:
        ----
            resname: The name of the 2DA resource to retrieve

        Returns:
        -------
            2DA: The retrieved 2DA data
        """
        if resname not in self._cache2da:
            twoda_result: ResourceResult | None = self.resource(
                resname,
                ResourceType.TwoDA,
                [SearchLocation.OVERRIDE, SearchLocation.CHITIN]
            )
            if twoda_result is not None:
                self._cache2da[resname] = read_2da(twoda_result.data)

        twoda_resource: TwoDA | None = self._cache2da.get(resname)
        if twoda_resource is None:
            assert_with_variable_trace(twoda_resource is not None, f"Lookup of resname '{resname}' cannot be None in {self!r}.htGetCache2DA(resname)")
            assert twoda_resource is not None
        return twoda_resource

    def htBatchCache2DA(
        self,
        resnames: list[str],
        *,
        reload: bool = False
    ):
        """Load missing 2DAs, replacing existing entries when reload=True."""
        queries: list[ResourceIdentifier] = [
            ResourceIdentifier(resname, ResourceType.TwoDA)
            for resname in resnames
            if reload or resname not in self._cache2da
        ]

        if not queries:
            return

        resources: dict[ResourceIdentifier, ResourceResult | None] = self.resources(queries, [SearchLocation.OVERRIDE, SearchLocation.CHITIN])
        for res_ident, resource in resources.items():
            if resource:
                self._cache2da[res_ident.resname] = read_2da(resource.data)

    # endregion

    # region Cache TPC
    def htGetCacheTPC(self, resname: str) -> TPC | None:
        """Gets cached TPC texture or loads and caches it."""
        tex_result: TPC | None = None
        if resname not in self._cacheTpc:
            tex_result = self.texture(resname, [SearchLocation.TEXTURES_TPA, SearchLocation.TEXTURES_GUI])
            if tex_result is not None:
                self._cacheTpc[resname] = tex_result
        else:
            tex_result = self._cacheTpc[resname]
        return tex_result


    # endregion

    def getItemIconFromUTI(self, uti: UTI | None) -> QPixmap:
        """Gets the item icon from the UTI, or a placeholder for a missing template."""
        pixmap = QPixmap(":/images/inventory/unknown.png")
        if uti is None:
            return pixmap
        baseitems: TwoDA = self.htGetCache2DA(HTInstallation.TwoDA_BASEITEMS)

        try:
            itemClass: str = baseitems.get_cell(uti.base_item, "itemclass")
            variation: int = uti.model_variation if uti.model_variation != 0 else uti.texture_variation
            textureResname = f'i{itemClass}_{str(variation).rjust(3, "0")}'
            texture: TPC | None = self.htGetCacheTPC(textureResname.lower())

            if texture is not None:
                return self._get_icon(texture)
        except Exception as e:
            print(format_exception_with_variables(e))
        return pixmap

    def getItemIcon(self, baseItem: int, modelVariation: int, textureVariation: int) -> QPixmap:
        """Get item icon from base item and variations."""
        pixmap = QPixmap(":/images/inventory/unknown.png")
        baseitems: TwoDA = self.htGetCache2DA(HTInstallation.TwoDA_BASEITEMS)

        with suppress(Exception):
            itemClass: str = baseitems.get_cell(baseItem, "itemclass")
            variation: int = modelVariation or textureVariation
            textureResname = f'i{itemClass}_{str(variation).rjust(3, "0")}'
            texture: TPC | None = self.htGetCacheTPC(textureResname.lower())

            if texture is not None:
                return self._get_icon(texture)
        return pixmap

    def _get_icon(self, texture):
        width, height, rgba = texture.convert(TPCTextureFormat.RGBA, 0)
        image = QImage(rgba, width, height, QImage.Format_RGBA8888)
        return QPixmap.fromImage(image).transformed(QTransform().scale(1, -1))

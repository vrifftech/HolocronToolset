from __future__ import annotations

from copy import deepcopy

from pykotor.resource.formats.tpc import TPC, TPCTextureFormat


def convert_texture(source: TPC, target: TPCTextureFormat, report) -> TPC:
    images = source.images()
    report(sum(len(image.mipmaps) for image in images), "set_maximum")
    converted = []
    progress = 0
    for image in images:
        mipmaps = []
        for level, mipmap in enumerate(image.mipmaps):
            report(f"Converting {image.kind} {image.index}, mip {level}", "update_subtask_text")
            if target in (TPCTextureFormat.DXT1, TPCTextureFormat.DXT5):
                pixels = source.convert(TPCTextureFormat.RGBA, level, image=image.index).data
                encoder = TPC.rgba_to_dxt1 if target == TPCTextureFormat.DXT1 else TPC.rgba_to_dxt5
                data = encoder(pixels, mipmap.width, mipmap.height)
            else:
                data = source.convert(target, level, image=image.index).data
            mipmaps.append(bytes(data))
            progress += 1
            report(progress, "set_value")
        converted.append(mipmaps)
    first = images[0].mipmaps[0]
    settings = source.effective_txi.settings()
    candidate = deepcopy(source)
    embedded = deepcopy(candidate.embedded_txi)
    candidate.set_images(first.width, first.height, converted, target, kind=images[0].kind,
                         numx=settings.texture["numx"], numy=settings.texture["numy"],
                         declared_mipmap_count=source.header.mipmap_count)
    candidate.embedded_txi = embedded
    return candidate

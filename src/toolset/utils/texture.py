from __future__ import annotations

from copy import deepcopy
from PIL import Image

from pykotor.resource.formats.tpc import TPC, TPCTextureFormat


def convert_texture(source: TPC, target: TPCTextureFormat, report) -> TPC:
    images = source.images()
    settings = source.effective_txi.settings(game=source.header.game)
    pvrtc = target in (TPCTextureFormat.PVRTC4_RGB, TPCTextureFormat.PVRTC4_RGBA)
    counts = []
    for image in images:
        first = image.mipmaps[0]
        count = len(image.mipmaps)
        if pvrtc and settings.texture["mipmap"]:
            count = max(count, max(first.width, first.height).bit_length())
        elif not pvrtc:
            count = min(count, min(first.width, first.height).bit_length())
        counts.append(count)
    report(sum(counts), "set_maximum")
    converted = []
    progress = 0
    for image, count in zip(images, counts):
        mipmaps = []
        for level in range(count):
            report(f"Converting {image.kind} {image.index}, mip {level}", "update_subtask_text")
            if target.is_compressed() and level < len(image.mipmaps):
                width, height, pixels = source.convert(TPCTextureFormat.RGBA, level, image=image.index)
            elif target.is_compressed():
                raster = Image.frombytes("RGBA", (width, height), bytes(pixels))
                width, height = max(1, width // 2), max(1, height // 2)
                pixels = raster.resize((width, height), Image.Resampling.BOX).tobytes()
            if pvrtc:
                data = TPC.rgba_to_pvrtc4(pixels, width, height, alpha=target == TPCTextureFormat.PVRTC4_RGBA)
            elif target in (TPCTextureFormat.DXT1, TPCTextureFormat.DXT5):
                encoder = TPC.rgba_to_dxt1 if target == TPCTextureFormat.DXT1 else TPC.rgba_to_dxt5
                data = encoder(pixels, width, height)
            else:
                data = source.convert(target, level, image=image.index).data
            mipmaps.append(bytes(data))
            progress += 1
            report(progress, "set_value")
        converted.append(mipmaps)
    first = images[0].mipmaps[0]
    candidate = deepcopy(source)
    embedded = deepcopy(candidate.embedded_txi)
    candidate.set_images(first.width, first.height, converted, target, kind=images[0].kind,
                         numx=settings.texture["numx"], numy=settings.texture["numy"],
                         declared_mipmap_count=(len(converted[0])
                                               if pvrtc or source.format() in (TPCTextureFormat.PVRTC4_RGB, TPCTextureFormat.PVRTC4_RGBA)
                                               else source.header.mipmap_count))
    candidate.embedded_txi = embedded
    return candidate

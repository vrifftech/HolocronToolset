from __future__ import annotations

from dataclasses import dataclass

from pykotor.resource.formats.gff import GFFFieldType, read_gff
from pykotor.resource.formats.lyt import read_lyt
from pykotor.resource.formats.mdl import read_mdl
from pykotor.resource.formats.ncs import NCSInstructionType, read_ncs
from pykotor.resource.formats.ncs.compiler.lexer import NssLexer
from pykotor.resource.formats.twoda import read_2da
from pykotor.resource.type import ResourceType
from toolset.data.resources import ResourceCatalog, ResourceLocation

GFF_TYPES = {restype for restype in ResourceType if restype.target_type().is_gff()} | {ResourceType.ITP_XML}
TABLE_TYPES = {ResourceType.TwoDA, ResourceType.TwoDA_CSV, ResourceType.TwoDA_JSON}
MODEL_TYPES = {ResourceType.MDL, ResourceType.MDL_ASCII}


@dataclass(frozen=True)
class ReferenceHit:
    resource: ResourceLocation
    kind: str
    field: str
    value: str


def reference_values(restype, data):
    if restype in GFF_TYPES:
        pending = [("GFF", GFFFieldType.Struct, read_gff(data).root)]
        while pending:
            path, field_type, value = pending.pop()
            if field_type == GFFFieldType.Struct:
                pending.extend((f"{path}.{label} [field {index}]", kind, child)
                               for index, (label, kind, child) in reversed(list(enumerate(value))))
            elif field_type == GFFFieldType.List:
                pending.extend((f"{path}[{index}]", GFFFieldType.Struct, child)
                               for index, child in reversed(list(enumerate(value))))
            elif field_type in (GFFFieldType.ResRef, GFFFieldType.String):
                yield field_type.name, path, str(value)
    elif restype in TABLE_TYPES:
        table = read_2da(data)
        headers = table.get_headers()
        for index, row in enumerate(table):
            for header in headers:
                yield "2DA cell", f"row {index} (label {row.label()}), column {header}", row.get_string(header)
    elif restype == ResourceType.NSS:
        lexer = NssLexer()
        lexer.input(data)
        token = lexer.token()
        while token is not None:
            if token.type == "STRING_VALUE":
                yield "NSS string", f"line {token.location.line}, column {token.location.column}", token.value.value
            token = lexer.token()
    elif restype == ResourceType.NCS:
        for index, instruction in enumerate(read_ncs(data).instructions):
            if instruction.ins_type == NCSInstructionType.CONSTS:
                yield "NCS string", f"instruction {index}", instruction.args[0]
    elif restype in MODEL_TYPES:
        model = read_mdl(data)
        yield "Supermodel", "supermodel", model.supermodel
        for index, node in enumerate(model.all_nodes()):
            path = f"node {index} ({node.name})"
            if node.mesh is not None:
                yield "Texture", f"{path}.texture_1", node.mesh.texture_1
                yield "Lightmap", f"{path}.texture_2", node.mesh.texture_2
            if node.light is not None:
                for flare, texture in enumerate(node.light.flare_textures):
                    yield "Flare texture", f"{path}.flare_textures[{flare}]", texture
    elif restype == ResourceType.LYT:
        layout = read_lyt(data)
        for label, entries in (("rooms", layout.rooms), ("tracks", layout.tracks), ("obstacles", layout.obstacles)):
            for index, entry in enumerate(entries):
                yield "Layout model", f"{label}[{index}].model", entry.model
        for index, hook in enumerate(layout.doorhooks):
            yield "Layout room", f"doorhooks[{index}].room", hook.room


def search_references(installation, filepath, query, *, case_sensitive, partial, models, report):
    base = ResourceCatalog.from_installation(installation, report, reload=True)
    catalog = base.with_context(filepath, report)
    kinds = GFF_TYPES | TABLE_TYPES | {ResourceType.NSS, ResourceType.NCS}
    if models:
        kinds.update(MODEL_TYPES | {ResourceType.LYT})
    records = [row for row in catalog.locations if row.restype in kinds]
    needle = query if case_sensitive else query.casefold()
    hits, errors = [], []
    report(len(records), "set_maximum")
    for index, record in enumerate(records):
        report(record.filename, "update_subtask_text")
        try:
            for kind, field, value in reference_values(record.restype, record.read()):
                candidate = value if case_sensitive else value.casefold()
                matched = needle in candidate if partial else needle == candidate
                if matched:
                    hits.append(ReferenceHit(record, kind, field, value))
        except Exception as error:
            errors.append((record, str(error)))
        if index % 64 == 0 or index == len(records) - 1:
            report(index + 1, "set_value")
    return base, tuple(hits), tuple(errors), len(records)

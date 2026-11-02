from __future__ import annotations

from dataclasses import dataclass

from pykotor.common.scriptdefs import KOTOR_CONSTANTS, KOTOR_FUNCTIONS, TSL_CONSTANTS, TSL_FUNCTIONS
from pykotor.common.scriptlib import KOTOR_LIBRARY, TSL_LIBRARY
from pykotor.resource.formats.ncs.compiler.classes import (
    FunctionDefinition, FunctionForwardDeclaration, GlobalVariableDeclaration,
    GlobalVariableInitialization, IncludeScript, IncludeSourceCache, StructDefinition,
)
from pykotor.resource.formats.ncs.compiler.lexer import NssLexer
from pykotor.resource.formats.ncs.compiler.parser import NssParser
from pykotor.resource.formats.ncs.compiler.source import CompileError, SourceDocument, SourceLocation


@dataclass(frozen=True)
class ScriptDefinition:
    name: str
    kind: str
    location: SourceLocation


@dataclass(frozen=True)
class ScriptAnalysis:
    root_name: str
    sources: dict[str, SourceDocument]
    definitions: tuple[ScriptDefinition, ...]
    error: CompileError | None


class AnalysisInclude(IncludeScript):
    def __init__(self, file, library, location, parse_source):
        super().__init__(file, library, location=location)
        self.parse_source = parse_source

    def _parse(self, root):
        name, source = self._get_script(root)
        return self.parse_source(source, name)


class AnalysisParser(NssParser):
    start = "code_root"

    def __init__(self, parse_source, *args, **kwargs):
        self.parse_source = parse_source
        super().__init__(*args, **kwargs)

    def p_include_script(self, p):
        """include_script : INCLUDE STRING_VALUE"""
        p[0] = AnalysisInclude(p[2], self.library, p.slice[1].location, self.parse_source)


def analyze_script(source, source_name, tsl, library_lookup, report):
    sources, definitions = {}, []
    cache = IncludeSourceCache()
    functions = TSL_FUNCTIONS if tsl else KOTOR_FUNCTIONS
    constants = TSL_CONSTANTS if tsl else KOTOR_CONSTANTS
    library = TSL_LIBRARY if tsl else KOTOR_LIBRARY

    def parse_source(text, name):
        report(f"Parsing {name}", "update_subtask_text")
        lexer = NssLexer(source_name=name)
        lexer.input(text)
        sources[name] = lexer.source
        locations = {}

        def next_token():
            token = lexer.token()
            if token is not None and token.type == "IDENTIFIER":
                locations[id(token.value)] = token.location
            return token

        parser = AnalysisParser(parse_source, functions, constants, library, library_lookup, include_cache=cache)
        parser.source = lexer.source
        root = parser.parser.parse(lexer=lexer.lexer, tracking=True, tokenfunc=next_token)
        for obj in root.objects:
            if isinstance(obj, FunctionDefinition):
                kind = "Function"
            elif isinstance(obj, FunctionForwardDeclaration):
                kind = "Prototype"
            elif isinstance(obj, StructDefinition):
                kind = "Struct"
            elif isinstance(obj, (GlobalVariableDeclaration, GlobalVariableInitialization)):
                kind = "Global"
            else:
                continue
            definitions.append(ScriptDefinition(obj.identifier.label, kind, locations[id(obj.identifier)]))
        return root

    error = None
    try:
        root = parse_source(source, source_name)
        root.expand_includes()
        report("Checking symbols and semantics", "update_subtask_text")
        root.register_symbols()
        root.validate_semantics()
    except CompileError as failure:
        error = failure
    return ScriptAnalysis(source_name, sources, tuple(definitions), error)

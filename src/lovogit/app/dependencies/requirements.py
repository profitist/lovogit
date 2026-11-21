import re
import shlex

from lovogit.app.dependencies.dto import (
    DiagnosticDTO,
    ParsedDeclarationsDTO,
    ProvenanceDTO,
)
from lovogit.app.dependencies.files import InventoryFileError, SnapshotReader
from lovogit.app.dependencies.versions import parse_requirement

_INCLUDE = re.compile(r"^(?:(-r|-c)\s*(.+)|(--requirement|--constraint)(?:\s+|=)(.+))$")
_HASH = re.compile(r"(?:^|\s)--hash(?:=|\s+)([^\s]+)")


def _logical_lines(text: str):
    pending = ""
    start = 1
    for number, line in enumerate(text.splitlines(), 1):
        if not pending:
            start = number
        if line.rstrip().endswith("\\") and not line.lstrip().startswith("#"):
            pending += line.rstrip()[:-1] + " "
            continue
        yield start, number, pending + line, False
        pending = ""
    if pending:
        yield start, number, pending, True


def _unquoted_characters(line: str):
    quote = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif quote is None:
            yield index, char


def _strip_comment(line: str) -> str:
    for index, char in _unquoted_characters(line):
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].strip()
    return line.strip()


def _extract_hashes(line: str):
    option_positions = {
        index
        for index, char in _unquoted_characters(line)
        if char == "-"
        and line.startswith("--", index)
        and (index == 0 or line[index - 1].isspace())
    }
    hashes, chunks = [], []
    end = 0
    for match in _HASH.finditer(line):
        position = line.index("--hash", match.start(), match.end())
        if position not in option_positions:
            continue
        option_positions.remove(position)
        hashes.append(match[1])
        chunks.append(line[end : match.start()])
        end = match.end()
    chunks.append(line[end:])
    return "".join(chunks).strip(), tuple(hashes), bool(option_positions)


def parse_requirements(reader: SnapshotReader) -> ParsedDeclarationsDTO:
    dependencies, constraints, diagnostics = [], [], []
    active = set()
    failed = False

    def visit(path: str, constraint: bool, depth: int, included_from: ProvenanceDTO):
        nonlocal failed
        identity = (path, constraint)
        if identity in active:
            diagnostics.append(DiagnosticDTO("include_cycle", included_from))
            return
        try:
            reader.consume_records(0, included_from, depth=depth)
            text = reader.read(path, included_from).decode("utf-8-sig")
        except (InventoryFileError, UnicodeError) as error:
            diagnostics.append(
                DiagnosticDTO(
                    error.code
                    if isinstance(error, InventoryFileError)
                    else "invalid_encoding",
                    included_from,
                    "error",
                )
            )
            if depth == 0:
                failed = True
            return
        active.add(identity)
        try:
            for start, end, raw, incomplete in _logical_lines(text):
                origin = ProvenanceDTO(path, start_line=start, end_line=end)
                reader.consume_records(1, origin)
                line = _strip_comment(raw)
                if not line:
                    continue
                if incomplete:
                    diagnostics.append(DiagnosticDTO("incomplete_continuation", origin))
                    continue
                if "${" in line:
                    diagnostics.append(
                        DiagnosticDTO("environment_substitution", origin)
                    )
                    continue
                include = _INCLUDE.fullmatch(line)
                if include:
                    flag = include[1] or include[3]
                    target = include[2] or include[4]
                    try:
                        tokens = shlex.split(target)
                        if len(tokens) != 1:
                            raise ValueError
                        child = reader.locate(tokens[0], origin, parent=path)
                    except (ValueError, InventoryFileError) as error:
                        diagnostics.append(
                            DiagnosticDTO(
                                error.code
                                if isinstance(error, InventoryFileError)
                                else "invalid_include",
                                origin,
                            )
                        )
                        continue
                    visit(child, flag in {"-c", "--constraint"}, depth + 1, origin)
                    continue
                editable = False
                if line.startswith(("-e ", "--editable ", "--editable=")):
                    editable = True
                    line = re.sub(r"^(?:-e\s+|--editable(?:\s+|=))", "", line)
                if line.startswith("-"):
                    diagnostics.append(DiagnosticDTO("unsupported_option", origin))
                    continue
                line, hashes, has_options = _extract_hashes(line)
                if has_options:
                    diagnostics.append(DiagnosticDTO("unsupported_option", origin))
                    continue
                try:
                    item = parse_requirement(
                        line, origin, hashes=hashes, editable=editable
                    )
                except ValueError:
                    diagnostics.append(
                        DiagnosticDTO(
                            "unsupported_editable"
                            if editable
                            else "invalid_requirement",
                            origin,
                        )
                    )
                    continue
                (constraints if constraint else dependencies).append(item)
                if item.version_state == "unsupported":
                    diagnostics.append(DiagnosticDTO("unsupported_source", origin))
        except InventoryFileError as error:
            diagnostics.append(DiagnosticDTO(error.code, error.origin))
        finally:
            active.remove(identity)

    visit("requirements.txt", False, 0, ProvenanceDTO("requirements.txt"))
    return ParsedDeclarationsDTO(
        tuple(dependencies), tuple(constraints), tuple(diagnostics), failed
    )

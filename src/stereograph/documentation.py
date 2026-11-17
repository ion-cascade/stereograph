"""Version-labelled lookup and full-text search over local Blender documentation."""

from functools import lru_cache
from html.parser import HTMLParser
import os
from pathlib import Path
import re

from stereograph_bridge.wire import failure, success

from .doc_install import CORPUS_LABEL, default_directory, is_installed
from .inspection_tools import READ_ONLY

SYMBOL = re.compile(r"(?:[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*\.?)?\*?|[A-Za-z_]\w*", re.ASCII)
DIRECTIVE = re.compile(r"^\s*\.\. (?:py:)?(?:attribute|class|data|function|method|module)\s*::\s*(.+)$", re.M)


class HtmlText(HTMLParser):
    """Keep document text and HTML identifier anchors."""

    def __init__(self):
        super().__init__()
        self.lines = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.suppressed += 1
        if tag in {"dt", "h1", "h2", "h3"}:
            identifier = dict(attrs).get("id")
            if identifier:
                self.lines.append(identifier)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.suppressed -= 1

    def handle_data(self, data):
        if not self.suppressed and data.strip():
            self.lines.append(data.strip())


def _configuration(corpus):
    root = os.environ.get("STEREOGRAPH_DOCS_ROOT")
    version = os.environ.get("STEREOGRAPH_DOCS_VERSION")
    if root:
        if not version:
            return None
        directory = Path(root).expanduser().resolve() / corpus
    else:
        installed = default_directory()
        if not is_installed(installed):
            return None
        directory, version = installed / corpus, CORPUS_LABEL
    return (directory, version) if directory.is_dir() else None


def _documents(directory):
    return sorted(path for path in directory.rglob("*")
                  if path.suffix in {".rst", ".html"}
                  and not any(part.startswith("_") for part in path.relative_to(directory).parts)
                  and path.stem not in {"genindex", "py-modindex", "search"})


@lru_cache(maxsize=4096)
def _content(path, modified_ns, size):
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".rst":
        return raw
    reader = HtmlText()
    reader.feed(raw)
    return "\n".join(reader.lines)


def _read(path):
    info = path.stat()
    if info.st_size > 8_000_000:
        return ""
    return _content(path, info.st_mtime_ns, info.st_size)


def _unavailable():
    return failure("docs_unavailable", "Run stereograph install-docs or set the local documentation root and version.")


def api_lookup(identifier, index=0):
    if not identifier or not SYMBOL.fullmatch(identifier) or len(identifier) > 512:
        return failure("invalid_identifier", "Use a Python identifier or trailing wildcard.")
    if index < 0:
        return failure("invalid_identifier", "Use a nonnegative index.")
    config = _configuration("api")
    if not config:
        return _unavailable()
    root, version = config
    pages = {path.stem: path for path in _documents(root)}
    if identifier.endswith("*"):
        prefix = identifier.removesuffix("*")
        available = (name for name in pages if name.startswith(prefix))
        if prefix.endswith(".") or not prefix:
            children = {prefix + name[len(prefix):].split(".")[0] for name in available}
        else:
            children = set(available)
        return success(dict(kind="namespace", identifier=identifier, version=version,
                            index=index, total=len(children), identifiers=sorted(children)[index:index + 500]))
    components = identifier.split(".")
    for size in range(len(components), 0, -1):
        page = pages.get(".".join(components[:size]))
        if page is None:
            continue
        content = _read(page)
        if size < len(components):
            member = ".".join(components[size:])
            lines = content.splitlines()
            for i, line in enumerate(lines):
                definition = DIRECTIVE.fullmatch(line)
                symbol = definition[1].split("(")[0] if definition else line.strip()
                if symbol in {member, identifier}:
                    end = next((j for j in range(i + 1, min(i + 50, len(lines)))
                                if DIRECTIVE.fullmatch(lines[j])), i + 35)
                    content = "\n".join(lines[i:end])
                    break
            else:
                continue
        if len(content) > 20_000:
            definitions = DIRECTIVE.findall(content)
            content = content[:4000] + "\n\nDefinitions\n" + "\n".join(definitions)
        return success(dict(kind="content", identifier=identifier, version=version,
                            path=page.relative_to(root).as_posix(), content=content[:20_000]))
    return failure("identifier_not_found", f"No documented API entry for {identifier!r}.")


def search_docs(corpus, query, max_results, context, index):
    if not query.strip() or len(query) > 512:
        return failure("bad_query", "Provide a search query of at most 512 characters.")
    if not 1 <= max_results <= 100 or not 0 <= context <= 10 or (index is not None and index < 0):
        return failure("bad_query", "Use 1–100 results, 0–10 context lines and a nonnegative index.")
    config = _configuration(corpus)
    if not config:
        return _unavailable()
    root, version = config
    terms = query.casefold().split()
    phrase = " ".join(terms)
    matches = []
    for page in _documents(root):
        content = _read(page)
        lowered = content.casefold()
        if not all(term in lowered for term in terms):
            continue
        relative = page.relative_to(root).as_posix()
        lines = content.splitlines()
        def relevance(i):
            line = " ".join(lines[i].casefold().split())
            return phrase in line, sum(term in line for term in terms)

        line_number = max(range(len(lines)), key=relevance)
        path_text = relative.casefold().replace("_", " ").replace("-", " ")
        score = (phrase in path_text, *relevance(line_number), sum(term in path_text for term in terms))
        excerpt = "\n".join(lines[max(0, line_number - context):line_number + context + 1])[:1500]
        matches.append((score, relative, line_number + 1, excerpt))
    matches.sort(key=lambda row: (tuple(-part for part in row[0]), row[1]))
    offset = 0 if index is None else index
    results = [dict(path=path, line=line, excerpt=excerpt)
               for _, path, line, excerpt in matches[offset:offset + max_results]]
    return success(dict(version=version, query=query, total=len(matches), index=offset, results=results))


def register_documentation_tools(mcp):
    @mcp.tool(annotations=READ_ONLY)
    def get_python_api_docs(identifier: str, index: int = 0) -> dict[str, object]:
        """Find a Python API identifier or discover qualified identifiers with a trailing wildcard."""
        return api_lookup(identifier, index)

    @mcp.tool(annotations=READ_ONLY)
    def search_api_docs(query: str, max_results: int = 20, context: int = 0,
                        index: int | None = None) -> dict[str, object]:
        """Find matching passages in the configured Blender Python API documentation."""
        return search_docs("api", query, max_results, context, index)

    @mcp.tool(annotations=READ_ONLY)
    def search_manual_docs(query: str, max_results: int = 20, context: int = 0,
                           index: int | None = None) -> dict[str, object]:
        """Find matching passages in the configured Blender manual."""
        return search_docs("manual", query, max_results, context, index)

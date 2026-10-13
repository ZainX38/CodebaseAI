import ast
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath
import re
import warnings

from app.indexing.chunking import Chunk
from app.retrieval.bm25 import tokenize


# Dotted names written in a question, e.g. "Session.send" or "requests.api.request"
QUESTION_IDENTIFIER = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")

# Words that are common method names but also normal English or very generic code.
# "send" or "json" alone in a question is not enough to pick one specific function
GENERIC_NAME_WORDS = {"get", "post", "put", "request", "send", "json", "text", "content", "call", "init"}

NAMED_WITH_OWNER = 30  # "Session.send", "auth._basic_auth_str"
NAMED_DIRECTLY = 25  # "should_strip_auth"
OWNER_AND_METHOD_WORDS = 20  # "HTTPDigestAuth ... handle a 401" --> HTTPDigestAuth.handle_401
CLASS_NAMED = 15  # "HTTPAdapter" --> its class header
CALLED_BY_MATCH = 10  # a helper called by a function named in the question

# A self.method() call in a class with hundreds of subclasses could match hundreds of
# overrides; only the closest few are kept
MAX_SELF_CALL_TARGETS = 3

SymbolKey = tuple[str, str]  # (qualified symbol, path), e.g. ("Session.send", "src/requests/sessions.py")


@dataclass(frozen=True)
class SymbolMatch:
    symbol: str
    path: str
    score: int
    reason: str


# A plain word like "send" is too generic to be a name on its own, but snake_case
# (prepare_body) or CamelCase (HTTPAdapter) names are specific enough
def is_distinctive(name: str) -> bool:
    stripped = name.strip("_")
    return "_" in stripped or any(character.isupper() for character in stripped[1:])


def last_part(qualified_name: str) -> str:
    return qualified_name.split(".")[-1]


# ---------------------- Everything known about the repository's symbols ------
# Built once per repository (not per question), because it parses every Python file.
# Classes are identified by (qualified name, path), so two classes called "Session"
# in different files (e.g. a fake one in tests/) never get mixed up
@dataclass
class SymbolTable:
    kinds: dict[SymbolKey, str] = field(default_factory=dict)
    chunks_by_symbol: dict[SymbolKey, list[Chunk]] = field(default_factory=dict)
    by_name: dict[str, list[SymbolKey]] = field(default_factory=dict)  # "send" --> every symbol called send
    classes_by_name: dict[str, list[SymbolKey]] = field(default_factory=dict)  # "Session" --> its class keys
    methods: dict[SymbolKey, dict[str, SymbolKey]] = field(default_factory=dict)  # class --> method name --> key
    parents: dict[SymbolKey, list[SymbolKey]] = field(default_factory=dict)  # class --> its base classes, in order
    children: dict[SymbolKey, list[SymbolKey]] = field(default_factory=dict)  # class --> classes inheriting it
    stubs: set[SymbolKey] = field(default_factory=set)  # methods whose body is only ... or pass
    imports: dict[str, dict[str, tuple[str, str]]] = field(default_factory=dict)  # path --> alias --> (path, name)

    @classmethod
    def build(cls, chunks: list[Chunk], files: dict[str, str]) -> "SymbolTable":
        table = cls()
        for chunk in chunks:
            if chunk.symbol:
                key = (chunk.symbol, chunk.path)
                table.kinds[key] = chunk.kind
                table.chunks_by_symbol.setdefault(key, []).append(chunk)

        for symbol, path in table.kinds:
            table.by_name.setdefault(last_part(symbol), []).append((symbol, path))
            if table.kinds[(symbol, path)] == "class":
                table.classes_by_name.setdefault(last_part(symbol), []).append((symbol, path))
            elif "." in symbol:
                owner = (symbol.rsplit(".", 1)[0], path)
                table.methods.setdefault(owner, {})[last_part(symbol)] = (symbol, path)

        module_index = get_module_index(files)
        trees = {path: parse_python(files[path]) for path in files if path.endswith(".py")}
        for path, tree in trees.items():
            if tree is not None:
                table.imports[path] = resolve_imports(path, tree, files, module_index)
        implemented: set[SymbolKey] = set()
        for path, tree in trees.items():
            if tree is not None:
                table._read_classes(path, tree, implemented)
        # @overload stubs share their name with the real method, so a name only counts as
        # a stub if it has no real implementation at all (e.g. Response.iter_content has
        # two "...: ..." overloads followed by the real definition)
        table.stubs -= implemented
        return table

    def _read_classes(self, path: str, tree: ast.Module, implemented: set[SymbolKey]) -> None:
        def visit(body: list[ast.stmt], prefix: str) -> None:
            for node in definitions(body):
                key = (prefix + node.name, path)
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        base_name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else None
                        parent = self.resolve_class(base_name, path) if base_name else None
                        if parent and parent != key:
                            self.parents.setdefault(key, []).append(parent)
                            self.children.setdefault(parent, []).append(key)
                    visit(node.body, key[0] + ".")
                elif is_stub(node):
                    self.stubs.add(key)
                else:
                    implemented.add(key)

        visit(tree.body, "")

    # A base class name is looked up like Python would: a class in the same file first,
    # then one imported into this file, then a class with that name anywhere (if only one exists)
    def resolve_class(self, name: str, path: str) -> SymbolKey | None:
        candidates = self.classes_by_name.get(name, [])
        same_file = [key for key in candidates if key[1] == path]
        if same_file:
            return same_file[0]
        if name in self.imports.get(path, {}):
            imported_path, imported_name = self.imports[path][name]
            if (imported_name, imported_path) in self.kinds:
                return (imported_name, imported_path)
        return candidates[0] if len(candidates) == 1 else None

    # Breadth-first, closest first: the class itself, its parents, their parents...
    # The "seen" set stops inheritance cycles from looping forever
    def ancestors(self, class_key: SymbolKey) -> list[SymbolKey]:
        return self._walk(class_key, self.parents)

    def descendants(self, class_key: SymbolKey) -> list[SymbolKey]:
        return self._walk(class_key, self.children)

    def _walk(self, start: SymbolKey, edges: dict[SymbolKey, list[SymbolKey]]) -> list[SymbolKey]:
        order, seen, queue = [], {start}, deque([start])
        while queue:
            current = queue.popleft()
            order.append(current)
            for neighbour in edges.get(current, []):
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        return order

    # "Session.send": the closest class in Session's ancestry that really implements send.
    # A stub ("def send(self): ...") in a mixin is skipped, and a parent's version is
    # ignored when the class overrides it
    def find_method(self, class_key: SymbolKey, method_name: str) -> SymbolKey | None:
        for ancestor in self.ancestors(class_key):
            method = self.methods.get(ancestor, {}).get(method_name)
            if method and method not in self.stubs:
                return method
        return None


# ---------------------- Finds the functions named in the question ------------
def find_named_symbols(question: str, table: SymbolTable, files: dict[str, str]) -> list[SymbolMatch]:
    identifiers = set(QUESTION_IDENTIFIER.findall(question))
    question_words = set(tokenize(question))
    matches: dict[SymbolKey, SymbolMatch] = {}

    def add(key: SymbolKey, score: int, reason: str) -> None:
        if key not in matches or score > matches[key].score:
            matches[key] = SymbolMatch(key[0], key[1], score, reason)

    for identifier in identifiers:
        parts = identifier.split(".")
        name, qualifier = parts[-1], (parts[-2] if len(parts) > 1 else None)

        # "Session.send" / "Store.finish_order": resolved through the class's ancestry
        if qualifier:
            for class_key in table.classes_by_name.get(qualifier, []):
                method = table.find_method(class_key, name)
                if method:
                    add(method, NAMED_WITH_OWNER, "named in the question with its class")

        for key in table.by_name.get(name, []):
            symbol, path = key
            kind = table.kinds[key]
            file_path = PurePosixPath(path)
            is_top_level = "." not in symbol
            # A top-level function is qualified by its module (api.request) or package
            # ("requests.get" means api.get re-exported by the package)
            module_qualified = is_top_level and qualifier in {file_path.stem, file_path.parent.name}

            if kind == "class":
                # A one-word class like "Request" also starts normal sentences, so it must
                # be qualified or distinctive. The header (class line, docstring) ranks lower
                if module_qualified or (identifier == name and is_distinctive(name)):
                    add(key, CLASS_NAMED, "class named in the question")
            elif module_qualified or identifier == symbol and "." in symbol:
                add(key, NAMED_WITH_OWNER, "named in the question with its module")
            elif identifier == name and is_distinctive(name) and key not in table.stubs:
                add(key, NAMED_DIRECTLY, "named in the question")

    # "How does HTTPDigestAuth handle a 401 challenge?" names the class but not the
    # method. A method of that class (or one it inherits) whose name shares words with
    # the question matches. Same rule as above: the class name must be distinctive
    for identifier in identifiers:
        if "." in identifier or not is_distinctive(identifier):
            continue
        for class_key in table.classes_by_name.get(identifier, []):
            for ancestor in table.ancestors(class_key):
                for method_name, method in table.methods.get(ancestor, {}).items():
                    if method_name.startswith("__") or method in table.stubs:
                        continue
                    shared_words = (set(tokenize(method_name)) & question_words) - GENERIC_NAME_WORDS
                    if shared_words:
                        add(method, OWNER_AND_METHOD_WORDS + len(shared_words), "class named and method name matches the question")

    # Helpers called directly by a named function, e.g. HTTPDigestAuth.handle_401 calls
    # self.build_digest_header, and rebuild_auth calls get_netrc_auth from utils.py
    for match in [match for match in matches.values() if match.score >= OWNER_AND_METHOD_WORDS]:
        for callee in resolve_callees(table, files, match):
            if callee not in matches:
                shared_words = len(set(tokenize(last_part(callee[0]))) & question_words)
                add(callee, CALLED_BY_MATCH + shared_words, f"called by {match.symbol}")

    return sorted(matches.values(), key=lambda match: (-match.score, match.path, match.symbol))


# ---------------------- Resolves what a named function calls -----------------
def resolve_callees(table: SymbolTable, files: dict[str, str], match: SymbolMatch) -> list[SymbolKey]:
    if not match.path.endswith(".py") or match.path not in files:
        return []

    owner = (match.symbol.rsplit(".", 1)[0], match.path) if "." in match.symbol else None
    resolved = []
    for callee_name, on_self in get_direct_callees(files[match.path], match.symbol):
        if on_self and owner in table.kinds:
            # The class's own ancestry first (normal method lookup). A mixin's self.send()
            # is often implemented by the class using the mixin, so subclasses come next
            targets = [table.find_method(owner, callee_name)]
            targets += [table.methods.get(child, {}).get(callee_name) for child in table.descendants(owner)[1:]]
            targets = [target for target in dict.fromkeys(targets) if target and target not in table.stubs]
            resolved.extend(targets[:MAX_SELF_CALL_TARGETS])
        elif (callee_name, match.path) in table.kinds:
            resolved.append((callee_name, match.path))
        elif callee_name in table.imports.get(match.path, {}):
            # A helper imported from another module, e.g. "from .utils import get_netrc_auth"
            imported_path, imported_name = table.imports[match.path][callee_name]
            if (imported_name, imported_path) in table.kinds:
                resolved.append((imported_name, imported_path))

    return list(dict.fromkeys(resolved))


# Returns (name, called on self/cls) for every call directly inside a function.
# Nested functions and classes are skipped: their calls belong to them, not to this function
def get_direct_callees(content: str, symbol: str) -> list[tuple[str, bool]]:
    node = find_definition(parse_python(content), symbol.split("."))
    if node is None:
        return []

    callees = []
    to_visit = deque(ast.iter_child_nodes(node))
    while to_visit:
        child = to_visit.popleft()
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                callees.append((child.func.id, False))
            elif (
                isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id in ("self", "cls")
            ):
                callees.append((child.func.attr, True))
        to_visit.extend(ast.iter_child_nodes(child))

    # dict.fromkeys() removes duplicates but keeps the order of the calls
    return list(dict.fromkeys(callees))


# ---------------------- AST helpers ------------------------------------------
# lru_cache is thread-safe, which matters because retrieval runs in asyncio.to_thread()
# and two questions (or a question and a repository build) can parse at the same time
@lru_cache(maxsize=4096)
def parse_python(content: str) -> ast.Module | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(content.removeprefix("﻿"))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


def is_stub(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]  # a docstring on its own still counts as a stub
    return all(
        isinstance(statement, ast.Pass)
        or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and statement.value.value is Ellipsis)
        or (isinstance(statement, ast.Raise) and "NotImplementedError" in ast.unparse(statement))
        for statement in body
    )


# Every definition with this qualified name, in file order. A name can be defined more
# than once: @overload stubs before the real method, or a property getter and setter
def find_definitions(tree: ast.Module | None, parts: list[str]) -> list[ast.AST]:
    if tree is None or not parts:
        return []

    body = tree.body
    for part in parts[:-1]:
        parent = next((child for child in definitions(body) if child.name == part), None)
        if parent is None:
            return []
        body = parent.body
    return [child for child in definitions(body) if child.name == parts[-1]]


def find_definition(tree: ast.Module | None, parts: list[str]) -> ast.AST | None:
    if tree is None:
        return None

    body, node = tree.body, None
    for part in parts:
        node = next((child for child in definitions(body) if child.name == part), None)
        if node is None:
            return None
        body = node.body
    return node


# Definitions directly in a body, also inside if/try/with blocks (same rule as chunking)
def definitions(body: list[ast.stmt]):
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            yield node
        elif isinstance(node, ast.If | ast.Try | ast.TryStar | ast.With | ast.AsyncWith):
            blocks = [node.body, getattr(node, "orelse", []), getattr(node, "finalbody", [])]
            blocks += [handler.body for handler in getattr(node, "handlers", [])]
            for block in blocks:
                yield from definitions(block)


# ---------------------- Import resolution ------------------------------------
# Every Python file under every dotted name it could be imported as:
# "src/requests/utils.py" --> "utils", "requests.utils", "src.requests.utils".
# Built once, so an import is a dictionary lookup instead of a scan over every file,
# and only whole module names match ("requests.utils" never matches "myrequests/utils.py")
def get_module_index(files: dict[str, str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for path in files:
        if not path.endswith(".py"):
            continue
        parts = list(PurePosixPath(path).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for start in range(len(parts)):
            index.setdefault(".".join(parts[start:]), []).append(path)
    return index


# "from .utils import get_netrc_auth as netrc" in src/requests/sessions.py
# --> {"netrc": ("src/requests/utils.py", "get_netrc_auth")}
def resolve_imports(
    path: str, tree: ast.Module, files: dict[str, str], module_index: dict[str, list[str]]
) -> dict[str, tuple[str, str]]:
    imports = {}
    package = PurePosixPath(path).parent

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.level:
            base = package
            for _ in range(node.level - 1):
                base = base.parent
            module_file = base / node.module.replace(".", "/")
            candidates = [str(module_file) + ".py", str(module_file / "__init__.py")]
            module_path = next((candidate for candidate in candidates if candidate in files), None)
        else:
            # If several files could be this module, the one closest to the importing file
            # wins (most shared folders), then the shortest path
            matching = module_index.get(node.module, [])
            module_path = min(
                matching,
                key=lambda candidate: (-len(shared_prefix(candidate, path)), len(candidate)),
                default=None,
            )

        if module_path:
            for alias in node.names:
                imports[alias.asname or alias.name] = (module_path, alias.name)
    return imports


def shared_prefix(first: str, second: str) -> list[str]:
    shared = []
    for a, b in zip(PurePosixPath(first).parts, PurePosixPath(second).parts):
        if a != b:
            break
        shared.append(a)
    return shared

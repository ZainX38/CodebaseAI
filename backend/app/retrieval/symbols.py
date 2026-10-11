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

        for path in files:
            tree = parse_python(files[path]) if path.endswith(".py") else None
            if tree is not None:
                table._read_classes(path, tree)
        return table

    def _read_classes(self, path: str, tree: ast.Module) -> None:
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

        visit(tree.body, "")

    # A base class name is looked up like Python would: a class in the same file first,
    # then a class with that name anywhere (if only one exists)
    def resolve_class(self, name: str, path: str) -> SymbolKey | None:
        candidates = self.classes_by_name.get(name, [])
        same_file = [key for key in candidates if key[1] == path]
        if same_file:
            return same_file[0]
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

    return sorted(matches.values(), key=lambda match: (-match.score, match.path, match.symbol))


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

from dataclasses import dataclass, field
import re
from typing import Any
from urllib.parse import quote

from app.rag.context import Excerpt


# Characters models put around an ID by mistake: "[source-1]", "(source-1)", "`source-1`"
WRAPPING_CHARACTERS = " \t\r\n\"'`[](){}<>"
# "source-1", "source 1", "source_1", "Source-1" all mean the same excerpt
SOURCE_ID = re.compile(r"source[\s_-]*(\d+)")


@dataclass
class CitationCheck:
    valid: list[Excerpt] = field(default_factory=list)  # each cited excerpt once, in citation order
    rejected: list[Any] = field(default_factory=list)  # raw values that don't name a supplied excerpt
    normalised: list[str] = field(default_factory=list)  # raw values that only matched after cleaning


# ---------------------- Normalises one citation ID ----------------------------
# Returns "source-N" or None. Only harmless formatting is removed: brackets, quotes,
# spaces and case. Anything else (e.g. "1" or a file name) is not guessed at
def normalise_citation_id(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None

    cleaned = raw.strip(WRAPPING_CHARACTERS).lower()
    # Models sometimes copy the whole header, e.g. "source-1 | 9f3a01c2" or "source-1 | code".
    # The part after "|" is the fence, which only marks where an excerpt starts and ends
    cleaned = cleaned.split("|", 1)[0].strip(WRAPPING_CHARACTERS)
    match = SOURCE_ID.fullmatch(cleaned)
    return f"source-{int(match.group(1))}" if match else None


# ---------------------- Validates the model's citations ----------------------
# A citation is only accepted if it is one of the excerpts that were actually sent
# to the model. Anything else is rejected, so the UI can never link to code the
# model didn't see (or to a made-up file)
def validate_citations(raw_citations: Any, excerpts: list[Excerpt]) -> CitationCheck:
    check = CitationCheck()
    if not isinstance(raw_citations, list):
        if raw_citations is not None:
            check.rejected.append(raw_citations)
        return check

    by_id = {excerpt.id: excerpt for excerpt in excerpts}
    for raw in raw_citations:
        citation_id = normalise_citation_id(raw)
        excerpt = by_id.get(citation_id) if citation_id else None

        if excerpt is None:
            check.rejected.append(raw)
            continue
        if raw != citation_id:
            check.normalised.append(raw)
        if excerpt not in check.valid:
            check.valid.append(excerpt)

    return check


# What the frontend gets for each valid citation: enough to open the file at the
# indexed commit and highlight the lines, plus a GitHub link for the same lines
def citation_to_dict(excerpt: Excerpt, owner: str, repo_name: str) -> dict[str, Any]:
    return {
        "id": excerpt.id,
        "path": excerpt.path,
        "start_line": excerpt.start_line,
        "end_line": excerpt.end_line,
        "symbol": excerpt.symbol,
        "commit_sha": excerpt.commit_sha,
        # Paths can contain spaces or "#", so every part of the URL is encoded
        "url": (
            f"https://github.com/{quote(owner, safe='')}/{quote(repo_name, safe='')}/blob/"
            f"{excerpt.commit_sha}/{quote(excerpt.path, safe='/')}#L{excerpt.start_line}-L{excerpt.end_line}"
        ),
    }

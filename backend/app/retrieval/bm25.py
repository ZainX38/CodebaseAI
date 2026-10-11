from collections import Counter, defaultdict
import math
import re


# Words that appear in almost every question but say nothing about the code
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for", "from", "how",
    "in", "is", "it", "its", "of", "on", "or", "that", "the", "this", "to", "what",
    "when", "where", "which", "who", "why", "with",
}

# [^\W\d] is "a letter or _" in any language, so café_naïve stays one identifier
IDENTIFIER = re.compile(r"[^\W\d]\w*|\d+")
# [^\W\d_A-Z] is "any letter except A-Z", so accented letters stay inside their word
CAMEL_CASE_PART = re.compile(r"[A-Z]+(?=[A-Z][^\W\d_A-Z])|[A-Z]?[^\W\d_A-Z]+|[A-Z]+|\d+")


# ---------------------- Tokenizer for code -----------------------------------
# Code names are made of several words: should_strip_auth, HTTPDigestAuth.
# Each identifier is kept whole (so an exact name matches strongly) AND split into
# its parts (so "strip auth" in a question still matches should_strip_auth)
def tokenize(text: str) -> list[str]:
    tokens = []
    for match in IDENTIFIER.finditer(text):
        identifier = match.group()
        whole = identifier.lower()
        if whole not in STOP_WORDS:
            tokens.append(whole)

        parts = [part.lower() for piece in identifier.split("_") for part in CAMEL_CASE_PART.findall(piece)]
        # "_helper" has one part, but "helper" in a question should still match it
        if len(parts) > 1 or (parts and parts[0] != whole):
            tokens.extend(part for part in parts if part not in STOP_WORDS)
    return tokens


# ---------------------- BM25 keyword search ----------------------------------
# Okapi BM25 scores a document higher when it contains the query words often,
# but with diminishing returns (k1), adjusted for document length (b), and with
# rare words counting more than common ones (IDF)
class BM25Index:
    def __init__(self, documents: dict[str, str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        term_counts = {doc_id: Counter(tokenize(text)) for doc_id, text in documents.items()}
        self.lengths = {doc_id: sum(counts.values()) for doc_id, counts in term_counts.items()}
        self.average_length = (sum(self.lengths.values()) / len(self.lengths)) if self.lengths else 0.0

        # Inverted index: term --> {document: how often}. A query only touches the
        # documents that contain its words, instead of looping over every chunk
        self.postings: dict[str, dict[str, int]] = defaultdict(dict)
        for doc_id, counts in term_counts.items():
            for term, frequency in counts.items():
                self.postings[term][doc_id] = frequency

        total = len(term_counts)
        # The "+ 1" inside the log keeps IDF positive even for words in most documents
        self.idf = {
            term: math.log((total - len(documents_with_term) + 0.5) / (len(documents_with_term) + 0.5) + 1)
            for term, documents_with_term in self.postings.items()
        }

    def score(self, query: str) -> dict[str, float]:
        scores: dict[str, float] = defaultdict(float)

        # dict.fromkeys() removes repeated words but keeps their order, so the sum is
        # always added up in the same order and the floats are identical every run
        for term in dict.fromkeys(tokenize(query)):
            for doc_id, frequency in self.postings.get(term, {}).items():
                length_ratio = self.lengths[doc_id] / self.average_length
                saturation = frequency * (self.k1 + 1) / (frequency + self.k1 * (1 - self.b + self.b * length_ratio))
                scores[doc_id] += self.idf[term] * saturation

        return dict(scores)

    # Best first. Ties keep a stable order (by ID) so results are reproducible
    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        ranked = sorted(self.score(query).items(), key=lambda item: (-item[1], item[0]))
        return ranked[:top_k]

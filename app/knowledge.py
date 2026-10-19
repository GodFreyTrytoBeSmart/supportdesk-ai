"""Small, auditable local retrieval over synthetic demo policies."""

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Article(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    content: str
    keywords: list[str] = Field(min_length=1)
    source: str
    verified_on: str


STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "have",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "please",
    "the",
    "this",
    "to",
    "us",
    "was",
    "what",
    "when",
    "where",
    "which",
    "who",
    "will",
    "with",
    "you",
    "your",
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower())) - STOP_WORDS


class KnowledgeBase:
    def __init__(self, path: Path | str | None = None):
        path = Path(path) if path else Path(__file__).resolve().parents[1] / "data" / "knowledge.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        self._articles = {article.id: article for article in map(Article.model_validate, rows)}
        if len(self._articles) != len(rows):
            raise ValueError("Knowledge article IDs must be unique")

    def get(self, article_id: str) -> Article | None:
        return self._articles.get(article_id)

    def search(self, query: str, limit: int = 3) -> list[Article]:
        """Rank exact keyword and word overlap; return no result without evidence.

        Keyword/title matches matter more than body matches. Stable article-ID
        tie breaking makes demonstration and evaluation results reproducible.
        This is lexical retrieval, not semantic or embedding-based retrieval.
        """
        normalized = " ".join(re.findall(r"[a-z0-9_]+", query.lower()))
        tokens = _tokens(query)
        if not tokens or limit <= 0:
            return []
        ranked = []
        for article in self._articles.values():
            keyword_tokens = _tokens(" ".join(article.keywords))
            score = 3.0 * len(tokens & keyword_tokens)
            score += 2.0 * len(tokens & _tokens(article.title))
            score += 0.1 * len(tokens & _tokens(article.content))
            for keyword in article.keywords:
                phrase = " ".join(re.findall(r"[a-z0-9_]+", keyword.lower()))
                if phrase and re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", normalized):
                    score += 4.0
            if score >= 2.0:
                ranked.append((score, article.id, article))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        return [row[2] for row in ranked[: min(limit, 10)]]

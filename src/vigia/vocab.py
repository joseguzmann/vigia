"""Role vocabulary and text normalization (see config/vocabulary.yaml)."""

from __future__ import annotations

import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

from vigia.config import ROOT

SEPARATORS_RE = re.compile(r"[-_/.·|,()\[\]+&:;!?\"'“”]+")


def normalize(text: str) -> str:
    """Lowercase, strip accents, separators to spaces: "Full-Stack/Back-End" → "full stack back end"."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", SEPARATORS_RE.sub(" ", text)).strip()


@lru_cache(maxsize=1)
def load() -> dict:
    explicit = os.environ.get("VIGIA_VOCAB")
    for p in ([Path(explicit)] if explicit else
              [ROOT / "config" / "vocabulary.yaml", Path("/app/config/vocabulary.yaml")]):
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    return {}


def variants(term: str) -> set[str]:
    """A term and its glued form: "back end" also matches "backend"."""
    t = normalize(term)
    out = {t}
    words = t.split()
    if len(words) == 2:
        out.add("".join(words))
    return out


def family_terms(families: list[str] | None = None) -> dict[str, set[str]]:
    fams = load().get("families", {})
    chosen = families or list(fams)
    out = {}
    for name in chosen:
        terms = set()
        for lang_terms in (fams.get(name) or {}).values():
            for term in lang_terms:
                terms |= variants(term)
        out[name] = terms
    return out


def match(text: str, terms: set[str]) -> str | None:
    """First term found as whole words in the normalized text, or None."""
    t = f" {normalize(text)} "
    for term in sorted(terms, key=len, reverse=True):
        if f" {term} " in t:
            return term
    return None


def search_phrases(families: list[str] | None = None) -> list[str]:
    search = load().get("search", {})
    out = []
    for name in families or list(search):
        out += search.get(name, [])
    return out


def reload() -> None:
    load.cache_clear()

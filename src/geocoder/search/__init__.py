"""
Search package — one resolution engine.

`parse_location` (address grammar) → `Resolver` (the ladder) → `SearchPipeline`
(thin wrapper returning ranked `GeoResult`s). `FuzzyRanker` reranks candidates.
"""
from geocoder.search.address import parse_location, ParsedLocation
from geocoder.search.fuzzy import FuzzyRanker, fuzzy_rerank
from geocoder.search.resolver import Resolver, Resolved
from geocoder.search.pipeline import SearchPipeline, resolve

__all__ = [
    "parse_location",
    "ParsedLocation",
    "FuzzyRanker",
    "fuzzy_rerank",
    "Resolver",
    "Resolved",
    "SearchPipeline",
    "resolve",
]

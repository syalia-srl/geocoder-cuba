"""
Fuzzy Re-ranking - Re-ranks FTS5 candidates using rapidfuzz.

Takes top-N FTS5 results and re-orders by string similarity
using WRatio (weighted ratio) for typo tolerance.
"""
from typing import List, Dict
from rapidfuzz import fuzz, process
from geocoder.config import SETTINGS


class FuzzyRanker:
    """
    Re-ranks FTS5 candidates using fuzzy string matching.
    
    Uses rapidfuzz.WRatio which handles:
    - Character transpositions
    - Missing/extra characters
    - Word order variations
    - Partial matches
    """
    
    def __init__(
        self,
        fuzzy_threshold: int = SETTINGS.FUZZY_THRESHOLD,
        fuzzy_limit: int = SETTINGS.FUZZY_LIMIT
    ):
        self.fuzzy_threshold = fuzzy_threshold
        self.fuzzy_limit = fuzzy_limit
    
    def rerank(
        self,
        query: str,
        candidates: List[Dict],
        tipo_boost: bool = True,
        municipio_boost: bool = True
    ) -> List[Dict]:
        """
        Re-rank candidates by fuzzy similarity to query.
        
        Args:
            query: Original user query (normalized).
            candidates: List of candidate dicts from FTS5.
            tipo_boost: Apply boost for portal > calle > poi.
            municipio_boost: Apply boost if candidate matches query's municipio.
            
        Returns:
            Re-ranked list (top fuzzy_limit) with score field added.
        """
        if not candidates:
            return []
        
        # Extract normalized names for matching
        names = [c.get('norm_nombre', '') for c in candidates]
        
        # Fuzzy match
        matches = process.extract(
            query,
            names,
            scorer=fuzz.WRatio,
            limit=self.fuzzy_limit
        )
        
        # Build scored results
        scored = []
        for match_name, score, idx in matches:
            if score < self.fuzzy_threshold:
                continue
            
            candidate = candidates[idx].copy()
            candidate['fuzzy_score'] = score
            
            # Apply type boost
            if tipo_boost:
                tipo = candidate.get('tipo', '')
                if tipo == 'portal':
                    score += SETTINGS.BOOST_PORTAL
                elif tipo == 'calle':
                    score += SETTINGS.BOOST_CALLE
                # poi gets 0 boost
            
            # Apply municipio boost (if query contained municipio filter)
            # This is handled upstream by filtering FTS query
            
            candidate['final_score'] = score
            scored.append(candidate)
        
        # Sort by final score descending
        scored.sort(key=lambda x: x['final_score'], reverse=True)
        
        return scored


def fuzzy_rerank(
    query: str,
    candidates: List[Dict],
    threshold: int = SETTINGS.FUZZY_THRESHOLD,
    limit: int = SETTINGS.FUZZY_LIMIT
) -> List[Dict]:
    """Convenience function for fuzzy re-ranking."""
    ranker = FuzzyRanker(fuzzy_threshold=threshold, fuzzy_limit=limit)
    return ranker.rerank(query, candidates)
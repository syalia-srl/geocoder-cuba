"""Utilidades de normalización de texto para matching robusto."""
import re
import unicodedata
from typing import List
from geocoder.config import SETTINGS


def strip_accents(text: str) -> str:
    """Elimina acentos y diacríticos."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def normalize_text(text: str) -> str:
    """Normaliza texto para búsqueda: lowercase, sin acentos, espacios colapsados."""
    if not text:
        return ""
    text = strip_accents(text.lower().strip())
    text = re.sub(r"[._\-/,;:]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_street_name(name: str) -> str:
    """Normaliza nombre de calle quitando prefijos tipo vía."""
    if not name:
        return ""
    n = normalize_text(name)
    for pref in SETTINGS.STREET_PREFIXES:
        if n.startswith(pref):
            n = n[len(pref):]
            break
    return _drop_placeholder_zero(n.strip())


def _drop_placeholder_zero(n: str) -> str:
    """Drop a bare `0` token from a street name.

    Legacy exports write a `0` where the house number is missing ("3ra. 0 e/ …",
    "lindero 0 y meireles"). Glued into the name it defeats the street match and
    costs the whole address ladder.

    Only a *non-leading* zero goes: the artifact always trails the name, while
    Havana has real streets called "0 (186)", "0 (cespedes)", "0 (frente sur)"
    whose zero leads. A bare "0" alone is also a real street, and is kept.

    Applied here rather than at a call site so the ETL writes and the parser
    reads the same key.
    """
    toks = n.split()
    if len(toks) < 2:
        return n
    kept = [t for i, t in enumerate(toks) if t != "0" or i == 0]
    return " ".join(kept)


def _fts_terms(text: str) -> List[str]:
    """Search terms safe to wrap in FTS5 quotes. A `"` inside a term ends the
    FTS5 string early and the query fails with "unterminated string"."""
    return normalize_text(text).replace('"', " ").split()


# Function words dropped from the strict FTS query. As prefixes they expand to a
# large share of the index, and FTS5 ranks every match before the LIMIT, so they
# cost most of a name lookup while narrowing nothing. The fuzzy rerank still
# sees the full name.
_FTS_SKIP = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "a"}


def normalize_for_fts(text: str) -> str:
    """Prepara términos para query FTS5: comodines en palabras, exacto en números
    y en letras sueltas (`"c"*` casaría con toda palabra que empiece por c)."""
    terms = _fts_terms(text)
    terms = [t for t in terms if t not in _FTS_SKIP] or terms
    parts = []
    for t in terms:
        parts.append(f'"{t}"' if t.isdigit() or len(t) == 1 else f'"{t}"*')
    return " AND ".join(parts)


def normalize_for_fts_fuzzy(text: str, prefix_len: int = 4) -> str:
    """Query FTS tolerante a typos: OR de prefijos cortos por término, de modo que
    un error de tecleo después del carácter `prefix_len` aún recupere candidatos
    (p.ej. 'coppellia' → '"copp"*'). Descarta palabras muy cortas (de, la, el)
    para no inundar. El reordenamiento fuzzy posterior elige el mejor."""
    parts = []
    for t in _fts_terms(text):
        if t.isdigit():
            parts.append(f'"{t}"')
        elif len(t) <= 2:
            continue
        elif len(t) <= prefix_len:
            parts.append(f'"{t}"*')
        else:
            parts.append(f'"{t[:prefix_len]}"*')
    return " OR ".join(parts)


def tokenize_for_fuzzy(text: str) -> List[str]:
    """Tokeniza para fuzzy matching."""
    return normalize_text(text).split()


_QUOTED = r'"([^"]+)"'
_TOKEN = r"([^\s,]+)"


def _extract_filter(query: str, keys: tuple[str, ...]) -> tuple[str, str | None]:
    """Non-greedy: value is a quoted string or a single token."""
    alt = "|".join(keys)
    pat = rf'\b(?:{alt})\s*:\s*(?:{_QUOTED}|{_TOKEN})'
    m = re.search(pat, query, re.IGNORECASE)
    if not m:
        return query, None
    value = m.group(1) if m.group(1) is not None else m.group(2)
    cleaned = re.sub(pat, "", query, count=1, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, value


def extract_municipio_filter(query: str) -> tuple[str, str | None]:
    """Extrae 'municipio:X' o 'muni:X' de la query."""
    return _extract_filter(query, ("municipio", "muni"))


def extract_barrio_filter(query: str) -> tuple[str, str | None]:
    """Extrae 'barrio:X' de la query."""
    return _extract_filter(query, ("barrio",))


def parse_intersection(query: str) -> tuple[bool, str, str]:
    """Detecta y parsea intersección 'A y B' o 'A e B'."""
    patterns = [
        r"^(.+?)\s+y\s+(.+)$",
        r"^(.+?)\s+e\s+(.+)$",
    ]
    for pat in patterns:
        m = re.match(pat, query.strip(), re.IGNORECASE)
        if m:
            return True, m.group(1).strip(), m.group(2).strip()
    return False, "", ""


def parse_portal(query: str) -> tuple[bool, str, str]:
    """Detecta patrón 'Calle Nombre 123' o 'Calle Nombre No 123'."""
    patterns = [
        r"^(.+?)\s+(?:no\.?|número|num\.?)\s*(\d+[a-z]?)$",
        r"^(.+?)\s+(\d+[a-z]?)$",
    ]
    for pat in patterns:
        m = re.match(pat, query.strip(), re.IGNORECASE)
        if m:
            street = m.group(1).strip()
            number = m.group(2).strip()
            if street and number:
                return True, street, number
    return False, "", ""


def clean_query(query: str) -> str:
    """Limpia query removiendo filtros ya extraídos."""
    _filter_pat = rf'\b(?:municipio|muni|barrio)\s*:\s*(?:{_QUOTED}|{_TOKEN})'
    query = re.sub(_filter_pat, "", query, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", query).strip()
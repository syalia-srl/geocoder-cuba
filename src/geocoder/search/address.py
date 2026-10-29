"""Parse free text into a structured Cuban location query."""
import re
from dataclasses import dataclass
from typing import Optional
from geocoder.utils.normalize import (
    normalize_text,
    normalize_street_name,
    extract_municipio_filter,
    extract_barrio_filter,
)


@dataclass
class ParsedLocation:
    raw: str
    kind: str = "place"          # "address" | "place" | "area"
    street: Optional[str] = None
    number: Optional[str] = None
    entre: Optional[tuple] = None      # (cross1, cross2)
    esquina: Optional[tuple] = None    # (a, b)
    name: Optional[str] = None
    barrio: Optional[str] = None
    municipio: Optional[str] = None
    provincia: Optional[str] = None
    reparto: Optional[str] = None


_NUM = r"(\d+[a-z]?)"
_ENTRE = r"(?:e/|entre|\s/\s)"
_ESQ = r"(?:esquina|esq\.?)\s*(?:a\s+)?"
# bare street-type words: "Calle 23" is the street "23", not house 23 on "Calle"
_BARE_STREET_WORDS = {"calle", "cll", "c", "avenida", "avda", "ave", "av",
                      "callejon", "pasaje", "paseo", "calzada"}


def parse_location(query: str, gazetteer=None) -> ParsedLocation:
    raw = (query or "").strip()
    p = ParsedLocation(raw=raw)
    if not raw:
        return p

    # Whole-query area: if the entire query is a known place (province/municipio/
    # barrio), it's an area — the gazetteer wins over the address grammar and the
    # POI search ("Cienfuegos", "diez de octubre", "Vedado").
    if gazetteer is not None and gazetteer.lookup(normalize_text(raw)):
        p.kind = "area"
        p.name = raw
        return p

    working = raw
    # context (reuse existing greedy-safe extractors)
    working, muni = extract_municipio_filter(working)
    working, barrio = extract_barrio_filter(working)
    p.municipio = muni or None
    p.barrio = barrio or None
    # extract trailing comma-separated context tokens (bare barrio/municipio names)
    # e.g. "Calle L #456 e/ 23 y 25, Vedado" → strip ", Vedado" as barrio
    if "," in working:
        parts = [pt.strip() for pt in working.split(",")]
        # trailing segments that look like context (no street markers) get absorbed
        while len(parts) > 1:
            tail = parts[-1]
            # a context token: no numbers, no street markers, short single/double word
            if tail and not re.search(r"[#/]|\b(?:e/|entre|esq)|\by\b|\d", tail, re.IGNORECASE):
                # the gazetteer, if present, assigns the right level; else default
                # to barrio (the scoping reference), then municipio, positionally.
                lvl = None
                if gazetteer is not None:
                    levels = {r["level"] for r in gazetteer.lookup(normalize_text(tail))}
                    lvl = ("barrio" if "barrio" in levels else
                           "municipio" if "municipio" in levels else
                           "provincia" if "provincia" in levels else None)
                if lvl == "municipio" and p.municipio is None:
                    p.municipio = tail
                elif lvl == "provincia" and p.provincia is None:
                    p.provincia = tail
                elif p.barrio is None:
                    p.barrio = tail
                elif p.municipio is None:
                    p.municipio = tail
                parts.pop()
            else:
                break
        working = ", ".join(parts)
    m = re.search(r"\breparto\s*:?\s*([^,]+)", working, re.IGNORECASE)
    if m:
        p.reparto = m.group(1).strip()
        working = re.sub(r"\breparto\s*:?\s*[^,]+", "", working, flags=re.IGNORECASE)
    # strip apto/edificio/piso (ignored for the point)
    working = re.sub(r"\b(?:apto|apartamento|edif(?:icio)?|piso)\.?\s*\S+", "", working, flags=re.IGNORECASE)
    working = re.sub(r"[,]", " ", working)
    working = re.sub(r"\s+", " ", working).strip()

    # house number (#/No./Nº/num)
    mnum = re.search(rf"(?:#|n[ºo]\.?|num\.?|no\.?)\s*{_NUM}", working, re.IGNORECASE)
    if mnum:
        p.number = mnum.group(1).lower()
        working = (working[:mnum.start()] + " " + working[mnum.end():]).strip()

    # entre: "<street?> e/ Y y Z"
    mentre = re.search(rf"^(.*?)\s*{_ENTRE}\s*(.+?)\s+y\s+(.+)$", working, re.IGNORECASE)
    if mentre:
        p.kind = "address"
        street_txt = mentre.group(1).strip()
        p.street = normalize_street_name(street_txt) or None
        p.entre = (normalize_street_name(mentre.group(2)), normalize_street_name(mentre.group(3)))
        return p

    # esquina: "A esq[.] [a] B"
    mesq = re.search(rf"^(.+?)\s+{_ESQ}\s*(.+)$", working, re.IGNORECASE)
    if mesq:
        p.kind = "address"
        p.esquina = (normalize_street_name(mesq.group(1)), normalize_street_name(mesq.group(2)))
        return p

    # bare "A y B" → esquina (intersection)
    myb = re.match(r"^(.+?)\s+y\s+(.+)$", working, re.IGNORECASE)
    if myb and p.number is None:
        p.kind = "address"
        p.esquina = (normalize_street_name(myb.group(1)), normalize_street_name(myb.group(2)))
        return p

    # bare "A/B" → esquina (common shorthand: "23/L"). Not the entre "e/" marker,
    # which is consumed above.
    mslash = re.match(r"^(.+?)\s*/\s*(.+)$", working)
    if mslash and p.number is None and not re.search(r"\be/?\s*$", mslash.group(1), re.IGNORECASE):
        p.kind = "address"
        p.esquina = (normalize_street_name(mslash.group(1)), normalize_street_name(mslash.group(2)))
        return p

    # street + number (portal), or street-only if number already captured
    if p.number is not None:
        p.kind = "address"
        p.street = normalize_street_name(working) or None
        return p
    mportal = re.match(rf"^(.+?)\s+{_NUM}$", working, re.IGNORECASE)
    if mportal:
        # "Calle 23" is the street named "23", not street "Calle" + house 23:
        # if everything before the number is just a street-type word, the number
        # IS the street name.
        if normalize_text(mportal.group(1)) in _BARE_STREET_WORDS:
            p.kind = "address"
            p.street = normalize_street_name(working) or None
            return p
        p.kind = "address"
        p.street = normalize_street_name(mportal.group(1)) or None
        p.number = mportal.group(2).lower()
        return p

    # a bare street-type prefix + name with no number → street (e.g. "Ave G")
    if re.match(r"^(?:calle|cll|avenida|avda|ave|av|callejon|pasaje|paseo|calzada)\s+\S",
                working, re.IGNORECASE):
        p.kind = "address"
        p.street = normalize_street_name(working) or None
        return p

    # otherwise a place/area name — kind refined by the resolver against the catalog
    p.name = working or None
    p.kind = "place"
    return p

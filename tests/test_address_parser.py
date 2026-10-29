from geocoder.search.address import parse_location

def test_portal():
    p = parse_location("Calle 23 #415")
    assert p.kind == "address" and p.street == "23" and p.number == "415"

def test_entre_with_number():
    p = parse_location("Calle L #456 e/ 23 y 25, Vedado")
    assert p.kind == "address"
    assert p.street == "l" and p.number == "456"
    assert p.entre == ("23", "25")
    assert p.barrio and p.barrio.lower() == "vedado"

def test_entre_no_number():
    p = parse_location("Calle L entre 23 y 25")
    assert p.entre == ("23", "25") and p.number is None

def test_esquina():
    p = parse_location("23 esq. a L")
    assert p.esquina == ("23", "l")
    assert p.entre is None

def test_a_y_b_is_esquina():
    p = parse_location("23 y 12")
    assert p.esquina == ("23", "12") and p.entre is None

def test_place():
    p = parse_location("Hotel Nacional")
    assert p.kind == "place" and p.name == "Hotel Nacional"

def test_number_first():
    p = parse_location("#460 e/ 19 y 21, Vedado")
    assert p.number == "460" and p.entre == ("19", "21")


def test_comma_before_esquina_not_absorbed():
    # "esquina L" tail must not be peeled as barrio context
    p = parse_location("Calle 23, esquina L")
    assert p.esquina == ("23", "l") and p.barrio is None


# ---- gazetteer-aware parsing (Task 6) ----

class _StubGaz:
    """Minimal gazetteer stub: only .lookup(norm) is used by the parser."""
    def __init__(self, data):
        self.data = data

    def lookup(self, norm):
        return self.data.get(norm, [])


_GZ = _StubGaz({
    "vedado": [{"level": "barrio", "canonical": "Vedado"}],
    "cienfuegos": [{"level": "provincia", "canonical": "Cienfuegos"},
                   {"level": "municipio", "canonical": "Cienfuegos"}],
    "diez de octubre": [{"level": "municipio", "canonical": "Diez de Octubre"}],
    "plaza de la revolucion": [{"level": "municipio", "canonical": "Plaza de la Revolución"}],
})


def test_gazetteer_context_barrio():
    p = parse_location("Calle L e/ 23 y 25, Vedado", gazetteer=_GZ)
    assert p.kind == "address" and p.entre == ("23", "25") and p.barrio == "Vedado"


def test_gazetteer_context_municipio_level():
    p = parse_location("Calle 23, Plaza de la Revolución", gazetteer=_GZ)
    assert p.municipio == "Plaza de la Revolución"


def test_gazetteer_whole_query_area():
    assert parse_location("Cienfuegos", gazetteer=_GZ).kind == "area"
    assert parse_location("diez de octubre", gazetteer=_GZ).kind == "area"


def test_gazetteer_does_not_break_addresses():
    p = parse_location("Calle 23", gazetteer=_GZ)
    assert p.kind == "address" and p.street == "23"


def test_backward_compatible_without_gazetteer():
    # no gazetteer → unchanged behaviour
    p = parse_location("Calle L e/ 23 y 25, Vedado")
    assert p.kind == "address" and p.barrio == "Vedado"


def test_placeholder_zero_is_not_part_of_the_street_name():
    """The source exports write a bare `0` where a house number is missing
    ('3ra. 0 e/ ...'). Glued into the street name it destroys the match."""
    p = parse_location("3ra. 0 e/ callejon llerena y final")
    assert p.kind == "address"
    assert p.street == "3ra"
    p = parse_location("d 0 e/ 9na. y lindero")
    assert p.street == "d"
    p = parse_location("lindero 0 y meireles")
    assert p.esquina == ("lindero", "meireles")


def test_placeholder_zero_stripped_from_cross_streets_too():
    p = parse_location("a e/ 0 y pino")
    assert p.entre == ("0", "pino") or p.entre[1] == "pino"


def test_a_street_actually_named_zero_survives():
    """'0' is a real street_norm in the vocabulary — a query that is only the
    zero must not be emptied out."""
    p = parse_location("calle 0 e/ 23 y 25")
    assert p.street == "0"
    assert p.entre == ("23", "25")


def test_leading_zero_street_is_preserved():
    """Real Havana streets are named '0 (186)' / '0 (cespedes)'. The placeholder
    artifact is always trailing, so only a non-leading bare 0 is dropped."""
    from geocoder.utils.normalize import normalize_street_name
    assert normalize_street_name("0 (186)") == "0 (186)"
    assert normalize_street_name("0 (cespedes)") == "0 (cespedes)"
    assert normalize_street_name("3ra. 0") == "3ra"

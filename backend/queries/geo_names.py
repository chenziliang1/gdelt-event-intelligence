"""
Place names as GDELT stores them versus as people write them.

GDELT's own export files carry six damaged Mexican state names: bytes after the accented
character are lost ("México" -> "Méco", "Yucatán" -> "YucatáMX"). The official files were
checked (db_scripts/repair_damaged_names.py): the damage is upstream, so events_table keeps the
source spelling and this module maps it, for region statistics and for search.
"""

from __future__ import annotations

import unicodedata
from typing import Dict, List

# raw ADM1 token in ActionGeo_FullName -> canonical name. Rows affected in 2024: 132,609 (MX).
DAMAGED_ADM1: Dict[str, str] = {
    "Méco": "Estado de México",
    "Nuevo LeóX": "Nuevo León",
    "Queréro de Arteaga": "Querétaro de Arteaga",
    "YucatáMX": "Yucatán",
    "Michoacáde Ocampo": "Michoacán de Ocampo",
    "San Luis PotosíX": "San Luis Potosí",
}


def fold(text: str) -> str:
    """Upper-case ASCII form used as a key: "Yucatán" and "yucatan" -> "YUCATAN"."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper().strip()


_RAW_BY_FOLDED: Dict[str, str] = {}
for _raw, _canonical in DAMAGED_ADM1.items():
    _RAW_BY_FOLDED[fold(_canonical)] = _raw
    # Short forms people use: "Querétaro", "Michoacán", "Estado de México" -> "México".
    _RAW_BY_FOLDED.setdefault(fold(_canonical.split(" de ")[0]), _raw)
_RAW_BY_FOLDED.pop(fold("Estado"), None)


def canonical_adm1(raw: str) -> str:
    return DAMAGED_ADM1.get(raw, raw)


def stored_adm1_spellings(term: str) -> List[str]:
    """How a state the user names is spelled in events_table, if GDELT damaged it.

    "Yucatan" -> ["YucatáMX"]; "Texas" -> [] (stored as written). "Mexico" alone is the
    country, so it is not mapped to the state.
    """
    folded = fold(term)
    if folded in ("MEXICO",):
        return []
    raw = _RAW_BY_FOLDED.get(folded)
    return [raw] if raw else []


def adm1_case_sql(column_expr: str) -> str:
    """SQL CASE turning a raw ADM1 expression into the canonical name."""
    whens = " ".join(
        "WHEN '{}' THEN '{}'".format(raw.replace("'", "''"), canonical.replace("'", "''"))
        for raw, canonical in DAMAGED_ADM1.items()
    )
    return f"(CASE {column_expr} {whens} ELSE {column_expr} END)"

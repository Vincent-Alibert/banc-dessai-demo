"""Campagnes de simulation S01, S02… ; tout autre mode est refusé."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from banc import chemins

RUNS = chemins.RUNS
SIMULATION = re.compile(r"S(0[1-9]|[1-9][0-9]+)")
MOTIF_TEST_SQL = r"^S(0[1-9]|[1-9][0-9]+)$"
FORMATS = "S01 à S99 puis S100… pour une simulation"


def code_valide(code: str) -> bool:
    return bool(SIMULATION.fullmatch(code or ""))


def est_test(code: str) -> bool:
    return code_valide(code)


def a_des_runs(code: str, runs: Path | None = None) -> bool:
    """La campagne a-t-elle deja des runs ecrits (runs/<C>/<C>-*.json) ? Le dossier seul ne compte pas : le verrou du
    lanceur le cree avant tout dialogue."""
    return any(((runs or RUNS) / code).glob(f"{code}-*.json"))


def scellee(dossier: Path) -> bool:
    """Le dossier d'une campagne ou d'un jeu (runs/<C>, demo/<jeu>) est-il sous scelle (juges/SCELLE : notation
    humaine a l'aveugle en cours) ? Seul critere du scelle pour les lanceurs et les rejeux."""
    return (dossier / "juges" / "SCELLE").exists()


def refus_source_scellee(source: Path) -> str | None:
    """None si `source` peut etre rejouee ; sinon le motif : rien ne sort d'une campagne scellee, ni ses dialogues
    ni ses notes. A appeler avant toute lecture de la source."""
    if scellee(source):
        return (
            f"source {source} scellee (notation humaine en cours) : ni ses dialogues ni ses notes ne sont lus "
            "ni rejoues"
        )
    return None


def refus_lancement(code: str, mode: str, runs: Path | None = None) -> str | None:
    if mode != "simulation":
        return "mode non supporté : simulation uniquement"
    if not code_valide(code):
        return f"campagne {code!r} : {FORMATS}"
    if scellee((runs or RUNS) / code):
        return f"campagne {code} scellée : lancement refusé"
    return None


def fichiers_de_runs(dossier: Path) -> list[Path]:
    """Les runs d'un dossier source de rejeu (demo/C01, runs/S01...) : <code>-*.json, avec un code valide."""
    return sorted(
        f for f in dossier.glob("*-*.json") if code_valide(f.name.split("-", 1)[0]) or f.name.startswith("C01-")
    )


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--verifier":
        sys.exit("usage : py -3.13 -m banc.campagnes.campagnes --verifier <campagne> <mode>")
    motif = refus_lancement(sys.argv[2], sys.argv[3])
    if motif:
        print(f"[X] {motif}")
        sys.exit(1)
    print(f"[ok] campagne {sys.argv[2]} permise en mode {sys.argv[3]}")

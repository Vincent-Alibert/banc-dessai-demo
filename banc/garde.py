"""Contrat commun : ce dossier ne sait exécuter que des simulations locales."""
from __future__ import annotations

import math
import os
import re
import sys
from pathlib import Path

from banc import chemins

MOTIF_CAMPAGNE = r"S(0[1-9]|[1-9][0-9]+)"
PERSONAS = ("claire", "jeanpierre", "lucas", "mehdi", "nadia")


def exiger_simulation(mode: str = "simulation") -> None:
    modes_cli = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--mode=")]
    for i, argument in enumerate(sys.argv[1:], start=1):
        if argument == "--mode":
            modes_cli.append(sys.argv[i + 1] if i + 1 < len(sys.argv) else "")
    if (mode != "simulation" or os.environ.get("BANC_MODE", "simulation") != "simulation"
            or any(m != "simulation" for m in modes_cli)):
        raise SystemExit("[X] mode non supporté : simulation uniquement ; aucun appel réel n'est disponible")


def campagne_valide(campagne: str) -> str:
    if not re.fullmatch(MOTIF_CAMPAGNE, campagne or ""):
        raise ValueError("campagne de simulation attendue : S01, S02… S100 (pas de mode réel)")
    return campagne


def source_autorisee(source: Path) -> Path:
    attendue = (chemins.JEUX_DEMO / "C01").resolve()
    if source.resolve() != attendue:
        raise ValueError("seul le jeu synthétique fourni demo/C01 est autorisé")
    return attendue


def nombre_positif(valeur: str) -> float:
    nombre = float(valeur)
    if not math.isfinite(nombre) or nombre < 0:
        raise ValueError("nombre fini positif ou nul attendu")
    return nombre


def personas_valides(texte: str) -> list[str]:
    ids = [p.strip() for p in texte.split(",") if p.strip()]
    if not ids or len(ids) != len(set(ids)) or set(ids) - set(PERSONAS):
        raise ValueError("personas attendus, sans doublon : " + ",".join(PERSONAS))
    return ids


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default="simulation")
    parser.add_argument("--campagne", required=True, type=campagne_valide)
    args = parser.parse_args()
    exiger_simulation(args.mode)

"""Précontrôles de la chaîne : mode, jeu synthétique, fiches, migrations et dbt local."""
from __future__ import annotations

import argparse
import sys

from banc import chemins
from banc.garde import campagne_valide, exiger_simulation, personas_valides
from banc.campagnes import campagnes
from banc.dialogues import dialoguer, simulation
from banc.personas.fiche_banc import lire_toutes
from entrepot.migrer import verifier
from entrepot.charger import executable_dbt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["simulation"], default="simulation")
    parser.add_argument("--campagne", required=True, type=campagne_valide)
    parser.add_argument("--personas", default="claire,jeanpierre,lucas,mehdi,nadia")
    args = parser.parse_args()
    exiger_simulation(args.mode)
    refus = campagnes.refus_lancement(args.campagne, args.mode)
    if refus:
        parser.error(refus)
    ids = personas_valides(args.personas)
    presentes = {f.identifiant for f in lire_toutes()}
    if set(ids) - presentes:
        parser.error("fiche synthétique absente")
    with simulation.reseau_interdit():
        try:
            simulation.installer(dialoguer, chemins.JEUX_DEMO / "C01", 0)
        finally:
            if hasattr(dialoguer, "_restaurer_simulation"):
                dialoguer._restaurer_simulation()
    verifier()
    executable_dbt()
    print("[ok] simulation exclusive, jeu intègre, fiches valides, migrations et dbt prêts")
    return 0


if __name__ == "__main__":
    sys.exit(main())

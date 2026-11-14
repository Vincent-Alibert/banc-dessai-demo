"""
Tache « personas » de la chaine : verifie que chaque persona demande a une fiche valide dans
personas/, qu'aucun n'est mineur, et affiche l'empreinte de chaque fiche (celle que le run
enregistrera, et que l'entrepot utilise pour sa dimension persona de type 2).
Les cinq fiches fournies sont synthétiques ; aucune génération n'est disponible.

Usage : python -m entrepot.verifier_personas --personas claire,mehdi
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from banc.personas.fiche_banc import DOSSIER_PERSONAS, lire  # noqa: E402
from banc.garde import campagne_valide, exiger_simulation, personas_valides


def main() -> int:
    args = sys.argv[1:]
    if "--personas" not in args:
        sys.exit("[X] --personas id1,id2 obligatoire")
    exiger_simulation()
    demandes = personas_valides(args[args.index("--personas") + 1])
    if not demandes:
        sys.exit("[X] aucun persona demande")
    for ident in demandes:
        chemin = DOSSIER_PERSONAS / f"{ident}.json"
        if not chemin.exists():
            sys.exit(f"[X] fiche absente : {chemin.relative_to(RACINE)}")
        fiche = lire(chemin)
        if fiche.axes.mineur:
            sys.exit(f"[X] {ident} : persona mineur, interdit dans le banc")
        empreinte = hashlib.sha256(fiche.model_dump_json().encode("utf-8")).hexdigest()[:16]
        print(f"  [ok] {ident:<12} {fiche.fiche.identite.prenom}, {fiche.fiche.identite.age} ans, "
              f"fiche {empreinte}")
    print(f"{len(demandes)} persona(s) pret(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

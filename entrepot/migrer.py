"""
Applique les migrations de l'entrepot du banc, dans l'ordre de leur version a 14 chiffres.

Chaque fichier entrepot/migrations/AAAAMMJJHHMMSS_nom.sql est joue dans UNE transaction, avec
son inscription dans banc_meta.migrations_appliquees (version, fichier, empreinte). Une
migration deja appliquee n'est pas rejouee ; si son fichier a change depuis, le script
s'arrete (une migration appliquee ne se modifie pas : on en ecrit une nouvelle).

Usage :  python entrepot/migrer.py            applique ce qui manque
         python entrepot/migrer.py --verifier  n'applique rien, sort en erreur s'il manque quelque chose
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from entrepot.connexion import connecter  # noqa: E402

DOSSIER = Path(__file__).resolve().parent / "migrations"
MOTIF = re.compile(r"^(\d{14})_[a-z0-9_]+\.sql$")

REGISTRE = """
CREATE SCHEMA IF NOT EXISTS banc_meta;
CREATE TABLE IF NOT EXISTS banc_meta.migrations_appliquees (
  version      text PRIMARY KEY,
  fichier      text NOT NULL,
  empreinte    text NOT NULL,
  appliquee_le timestamptz NOT NULL DEFAULT now()
);
"""


def fichiers() -> list[tuple[str, Path, str]]:
    liste = []
    for f in sorted(DOSSIER.glob("*.sql")):
        m = MOTIF.match(f.name)
        if not m:
            raise SystemExit(f"[X] nom de migration invalide : {f.name} (attendu AAAAMMJJHHMMSS_nom.sql)")
        # empreinte sur le texte aux fins de ligne normalisees : une extraction git en CRLF
        # ne doit pas faire passer une migration appliquee pour modifiee
        texte = f.read_bytes().replace(bytes([13, 10]), bytes([10]))
        liste.append((m.group(1), f, hashlib.sha256(texte).hexdigest()[:16]))
    versions = [v for v, _, _ in liste]
    if len(versions) != len(set(versions)):
        raise SystemExit("[X] deux migrations partagent une version")
    return liste


def etat(cnx) -> tuple[list, list]:
    """(manquantes, modifiees) par rapport au registre."""
    cnx.execute(REGISTRE)
    appliquees = dict(cnx.execute("SELECT version, empreinte FROM banc_meta.migrations_appliquees").fetchall())
    inconnues = set(appliquees) - {v for v, _, _ in fichiers()}
    if inconnues:
        raise SystemExit("[X] registre d'une autre installation : utiliser un nouveau projet/volume de démo")
    manquantes, modifiees = [], []
    for version, f, empreinte in fichiers():
        if version not in appliquees:
            manquantes.append((version, f, empreinte))
        elif appliquees[version] != empreinte:
            modifiees.append(f.name)
    return manquantes, modifiees


def verifier() -> None:
    """Leve SystemExit si une migration manque ou a change. Appele par le chargeur."""
    with connecter(autocommit=True) as cnx:
        manquantes, modifiees = etat(cnx)
    if modifiees:
        raise SystemExit(f"[X] migration(s) modifiee(s) apres application : {', '.join(modifiees)}")
    if manquantes:
        raise SystemExit("[X] migration(s) non appliquee(s) : "
                         + ", ".join(f.name for _, f, _ in manquantes)
                         + "\n    Lancer : python entrepot/migrer.py")


def main() -> int:
    if "--verifier" in sys.argv:
        verifier()
        print("[ok] entrepot a jour")
        return 0
    with connecter(autocommit=True) as cnx:
        manquantes, modifiees = etat(cnx)
    if modifiees:
        raise SystemExit(f"[X] migration(s) modifiee(s) apres application : {', '.join(modifiees)}")
    if not manquantes:
        print("[ok] aucune migration a appliquer")
        return 0
    for version, f, empreinte in manquantes:
        with connecter() as cnx:          # une transaction par migration
            cnx.execute(f.read_text(encoding="utf-8"))
            cnx.execute("INSERT INTO banc_meta.migrations_appliquees (version, fichier, empreinte) "
                        "VALUES (%s, %s, %s)", (version, f.name, empreinte))
        print(f"[ok] {f.name} ({empreinte})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

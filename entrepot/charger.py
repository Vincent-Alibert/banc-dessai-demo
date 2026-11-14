"""Charge les seuls fichiers d'une campagne simulée, puis dbt build et dbt docs generate.
Les dépenses et les jetons nouveaux sont obligatoirement nuls ; le budget simulé reste distinct.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from banc import chemins  # noqa: E402
from entrepot.connexion import connecter  # noqa: E402
from entrepot.migrer import verifier  # noqa: E402
from banc.garde import campagne_valide, exiger_simulation  # noqa: E402

DOSSIER_DBT = RACINE / "dbt"


def executable_dbt() -> str:
    """dbt epingle : DBT_BIN, sinon l'environnement de l'image Airflow, sinon .venv-dbt du depot."""
    import os
    import shutil
    for candidat in (os.environ.get("DBT_BIN"), "/opt/dbt-venv/bin/dbt",
                     str(RACINE / ".venv-dbt" / "Scripts" / "dbt.exe"), str(RACINE / ".venv-dbt" / "bin" / "dbt")):
        if candidat and Path(candidat).exists():
            return candidat
    trouve = shutil.which("dbt")
    if not trouve:
        raise SystemExit("[X] dbt introuvable : python -m venv .venv-dbt puis "
                         "utiliser la pile Docker fournie")
    return trouve


def dbt(*commande: str) -> None:
    """Lance dbt avec la connexion de l'entrepot (memes regles que entrepot/connexion.py)."""
    import os
    import subprocess
    from urllib.parse import unquote, urlparse
    from entrepot.connexion import dsn
    u = urlparse(dsn())
    env = {**os.environ, "BANC_DBT_HOTE": u.hostname or "127.0.0.1", "BANC_DBT_PORT": str(u.port or 5432),
           "BANC_DBT_UTILISATEUR": unquote(u.username or "banc"), "BANC_DBT_MOT_DE_PASSE": unquote(u.password or ""),
           "BANC_DBT_BASE": (u.path or "/banc").lstrip("/"), "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    print(f"dbt {' '.join(commande)}")
    fin = subprocess.run([executable_dbt(), *commande, "--project-dir", str(DOSSIER_DBT),
                          "--profiles-dir", str(DOSSIER_DBT)], env=env, cwd=DOSSIER_DBT)
    if fin.returncode != 0:
        raise SystemExit(f"[X] dbt {' '.join(commande)} a echoue (code {fin.returncode}) : etoile non validee, "
                         "chaine arretee avant le calcul d'accord")


RUNS = chemins.RUNS
SEULE = None


def empreinte(octets: bytes) -> str:
    return hashlib.sha256(octets).hexdigest()[:16]


def upsert(cnx, table: str, cle: str, valeurs: dict) -> bool:
    """Insere ou met a jour si l'empreinte a change. Renvoie True si quelque chose a ete ecrit."""
    colonnes = list(valeurs)
    sql = (f"INSERT INTO {table} ({', '.join(colonnes)}) VALUES ({', '.join(['%s'] * len(colonnes))}) "
           f"ON CONFLICT ({cle}) DO UPDATE SET "
           + ", ".join(f"{c} = EXCLUDED.{c}" for c in colonnes if c != cle)
           + ", charge_le = now() "
           + (f"WHERE {table}.empreinte IS DISTINCT FROM EXCLUDED.empreinte" if "empreinte" in colonnes else ""))
    return cnx.execute(sql, list(valeurs.values())).rowcount > 0


def lire_grilles() -> list[tuple[str, list[dict]]]:
    return [lire_grille(chemins.PROMPT_JUGE)]


def lire_grille(fichier: Path) -> tuple[str, list[dict]]:
    texte = fichier.read_text(encoding="utf-8")
    version = re.search(r"version (\d+\.\d+)", texte).group(1)
    axes = []
    for m in re.finditer(r"^(B[0-6]) ([^.]+)\. (.+)$", texte, re.M):
        # « sans objet » possible : B6 dans toutes les grilles ; depuis la v1.3, tout axe dont la
        # definition prevoit le null (B4 et B5, dialogue de moins de 3 tours)
        axes.append({"axe_id": m.group(1), "libelle": m.group(2).strip(), "definition": m.group(3).strip(),
                     "sans_objet_possible": m.group(1) == "B6" or "(null)" in m.group(3)})
    attendus = 7 if version != "1.0" else 6   # B0 ajoute en v1.1
    if len(axes) != attendus:
        raise SystemExit(f"[X] grille : {len(axes)} axes lus dans {fichier.name}, {attendus} attendus")
    return f"B-v{version}", axes


def valider_run(run: dict, campagne: str) -> None:
    if run.get("campagne") != campagne or run.get("bras_banc") != "simulation" or run.get("type_run") != "dialogue":
        raise ValueError("le chargeur refuse tout run non simulé ou hors campagne")
    if run.get("provenance", {}).get("mode") != "simulation":
        raise ValueError("provenance de simulation absente")
    cout = run.get("cout") or {}
    for poste in ("dialogue", "onboarding", "analyse"):
        if any(cout.get(poste, {}).get(k) != 0 for k in ("cout_usd", "appels", "tokens")):
            raise ValueError("dépense, appel API ou jeton non nul dans une simulation")
    simule = cout.get("simule_usd")
    if type(simule) not in (int, float) or not math.isfinite(simule) or simule < 0:
        raise ValueError("coût simulé distinct, fini et positif attendu")


def main() -> int:
    global SEULE
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campagne", required=True, type=campagne_valide)
    args = parser.parse_args()
    exiger_simulation()
    SEULE = args.campagne
    verifier()
    dossier = RUNS / SEULE
    runs = sorted(dossier.glob(f"{SEULE}-*.json"))
    if not runs:
        raise SystemExit("[X] aucun run simulé pour la campagne demandée")
    compte = {"run": 0, "fiche_persona": 0, "appel_juge": 0}
    with connecter() as cnx:
        presents = []
        for f in runs:
            octets = f.read_bytes()
            run = json.loads(octets)
            valider_run(run, SEULE)
            nom = f"{SEULE}/{f.name}"
            presents.append(nom)
            compte["run"] += upsert(cnx, "banc_brut.run", "fichier", {
                "fichier": nom, "campagne": SEULE, "empreinte": empreinte(octets), "contenu": json.dumps(run)})
        cnx.execute("DELETE FROM banc_brut.run WHERE campagne = %s AND NOT fichier = ANY(%s)", (SEULE, presents))
        from banc.personas.fiche_banc import lire
        for f in sorted(chemins.PERSONAS.glob("*.json")):
            lire(f)
            octets = f.read_bytes()
            compte["fiche_persona"] += upsert(cnx, "banc_brut.fiche_persona", "fichier", {
                "fichier": f.name, "empreinte": empreinte(octets), "contenu": json.dumps(json.loads(octets))})
        presents = []
        for f in sorted(dossier.glob("juges/*/*.json")):
            octets = f.read_bytes()
            note = json.loads(octets)
            juge = f.parent.name
            if juge not in ("factice", "factice-b") or note.get("juge") != juge or note.get("cout_usd") != 0:
                raise ValueError("le chargeur accepte uniquement les deux juges factices sans dépense")
            if any(note.get("usage", {}).get(k, 0) != 0 for k in ("entree", "sortie", "reflexion", "essais_factures")):
                raise ValueError("un juge factice ne facture aucun jeton ni essai")
            nom = f"{SEULE}/juges/{juge}/{f.name}"
            presents.append(nom)
            compte["appel_juge"] += upsert(cnx, "banc_brut.appel_juge", "fichier", {
                "fichier": nom, "campagne": SEULE, "juge": juge, "empreinte": empreinte(octets),
                "contenu": json.dumps(note)})
        cnx.execute("DELETE FROM banc_brut.appel_juge WHERE campagne = %s AND NOT fichier = ANY(%s)", (SEULE, presents))
        corr = dossier / "notation" / "correspondance.json"
        if not corr.exists():
            raise ValueError("correspondance de notation absente")
        octets = corr.read_bytes()
        upsert(cnx, "banc_brut.correspondance_notation", "campagne", {
            "campagne": SEULE, "empreinte": empreinte(octets), "contenu": json.dumps(json.loads(octets))})
        controle = dossier / "controles" / "persona.json"
        if not controle.exists():
            raise ValueError("contrôles des personas absents")
        octets = controle.read_bytes()
        upsert(cnx, "banc_brut.controle_persona", "fichier", {
            "fichier": f"{SEULE}/controles/persona.json", "campagne": SEULE,
            "empreinte": empreinte(octets), "contenu": json.dumps(json.loads(octets))})
        ligne = {"campagne_id": SEULE, "version_fiches": "synthétique C01", "base": "simulation",
                 "objet": "Vrai lanceur avec doubles enregistrés C01 ; aucun appel externe, aucun jugement réel."}
        cnx.execute("INSERT INTO banc_brut.campagne (campagne_id, ligne) VALUES (%s, %s) "
                    "ON CONFLICT (campagne_id) DO UPDATE SET ligne = EXCLUDED.ligne, charge_le = now()",
                    (SEULE, json.dumps(ligne)))
        for version, axes in lire_grilles():
            cnx.execute("INSERT INTO banc_brut.grille (version_grille, axes) VALUES (%s, %s) "
                        "ON CONFLICT (version_grille) DO UPDATE SET axes = EXCLUDED.axes, charge_le = now()",
                        (version, json.dumps(axes)))
    print("couche brute : " + ", ".join(f"{k} {v} écrit(s)" for k, v in compte.items()))
    dbt("build")
    dbt("docs", "generate")
    with connecter() as cnx:
        bilan = cnx.execute("SELECT (SELECT count(*) FROM banc.dim_run), (SELECT count(*) FROM banc.fait_tour), "
                            "(SELECT count(*) FROM banc.fait_note)").fetchone()
    print("étoile : {} runs · {} tours · {} notes ; tests dbt réussis".format(*bilan))
    return 0


if __name__ == "__main__":
    sys.exit(main())

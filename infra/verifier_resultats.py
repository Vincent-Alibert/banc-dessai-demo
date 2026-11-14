"""Recette post-DAG : données Postgres, tests dbt, rapport et requêtes Metabase réellement accessibles."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from banc import chemins
from banc.garde import campagne_valide, exiger_simulation
from entrepot.charger import valider_run
from entrepot.connexion import connecter


def verifier(campagne):
    campagne_valide(campagne)
    exiger_simulation()
    dossier = chemins.RUNS / campagne
    fichiers = sorted(dossier.glob(f"{campagne}-*.json"))
    if not fichiers:
        raise ValueError("aucun run simulé écrit")
    runs = [json.loads(f.read_text(encoding="utf-8")) for f in fichiers]
    for run in runs:
        valider_run(run, campagne)
    correspondance = json.loads((dossier / "notation/correspondance.json").read_text(encoding="utf-8"))["dialogues"]
    tours = sum(len(r["tours"]) for r in runs)
    notes = len(correspondance) * 2 * 7
    cout_simule = round(sum(r["cout"]["simule_usd"] for r in runs), 6)
    with connecter() as cnx:
        lu = cnx.execute("""
            SELECT
              (SELECT count(*) FROM banc.dim_run WHERE campagne_id = %s),
              (SELECT count(*) FROM banc.fait_tour t JOIN banc.dim_run r USING (run_id) WHERE r.campagne_id = %s),
              (SELECT count(*) FROM banc.fait_note n JOIN banc.dim_run r USING (run_id) WHERE r.campagne_id = %s),
              (SELECT coalesce(sum(cout_usd), 0) FROM banc.fait_cout WHERE campagne_id = %s),
              (SELECT count(*) FROM banc.fait_accord WHERE campagne_id = %s AND mesure IN ('alpha_ordinal','kappa_quadratique')),
              (SELECT cout_simule_usd FROM banc.v_couts_simules WHERE campagne_id = %s),
              (SELECT count(*) FROM banc.dim_run WHERE campagne_id = %s AND mode_execution IS DISTINCT FROM 'simulation')
        """, (campagne,) * 7).fetchone()
    if tuple(lu[:3]) != (len(runs), tours, notes) or lu[3] != 0 or lu[4] <= 0 or lu[6] != 0:
        raise ValueError("comptages, dépenses nulles, accord ou mode de l'entrepôt incohérents")
    if not math.isclose(float(lu[5]), cout_simule, abs_tol=0.000001):
        raise ValueError("budget simulé mélangé ou perdu dans l'entrepôt")
    resultats = json.loads((chemins.RACINE / "dbt/target/run_results.json").read_text(encoding="utf-8"))
    if not resultats.get("results") or any(r.get("status") not in ("success", "pass") for r in resultats["results"]):
        raise ValueError("dbt build incomplet ou en échec")
    for fichier in (dossier / "rapport_v0.html", chemins.RACINE / "dbt/target/index.html"):
        if not fichier.is_file() or fichier.stat().st_size == 0:
            raise ValueError("rapport ou documentation dbt absent")
    meta = json.loads((dossier / "metabase.json").read_text(encoding="utf-8"))
    if meta.get("mode") != "simulation" or meta.get("campagne") != campagne or len(meta.get("questions", [])) != 6:
        raise ValueError("tableau de simulation Metabase incomplet")
    from infra.metabase_configurer import appel, session
    jeton = session()
    for q in meta["questions"]:
        resultat = appel("POST", f"/api/card/{int(q['id'])}/query", {}, session=jeton)
        if resultat.get("error") or resultat.get("status") == "failed":
            raise ValueError("question Metabase inaccessible")
        effectif = resultat.get("row_count")
        if effectif is None:
            effectif = len((resultat.get("data") or {}).get("rows") or [])
        if effectif <= 0 and not q.get("vide_autorise"):
            raise ValueError("question Metabase centrale vide")
    bilan = {"campagne": campagne, "mode": "simulation", "runs": len(runs), "tours": tours, "notes_axes": notes,
             "depenses_usd": 0, "cout_simule_usd": cout_simule, "tests_dbt": "réussis",
             "questions_metabase_accessibles": len(meta["questions"]), "dashboard_path": meta["dashboard_path"]}
    (dossier / "verification.json").write_text(json.dumps(bilan, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[ok] recette complète de cette exécution : " + json.dumps(bilan, ensure_ascii=False))
    return bilan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campagne", required=True, type=campagne_valide)
    args = parser.parse_args()
    verifier(args.campagne)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        raise SystemExit("[X] " + str(exc)) from None

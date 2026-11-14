"""Configuration rejouable de Metabase, uniquement pour les campagnes S* simulées.
Les questions s'appuient sur les vues non filtrées de l'étoile et sur deux juges factices.
Aucun fichier d'environnement, aucune clé API, aucun partage public.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

from banc import chemins
from banc.garde import campagne_valide, exiger_simulation

URL = os.environ.get("METABASE_URL", "http://127.0.0.1:13030").rstrip("/")
# Identifiants publics de cette pile de démo seulement, jamais ceux d'une autre installation.
ENV = {"MB_ADMIN_EMAIL": "demo@banc.local", "MB_ADMIN_PASSWORD": "demo-banc-noa-2026",
       "METABASE_LECTURE_PASSWORD": "demo-lecture-local"}
NOM_BASE = "Entrepôt de simulation Noa"
NOM_COLLECTION = "Simulations Noa"
_RESULTATS_QUESTIONS = []


def questions_simulation(campagne: str) -> list[dict]:
    campagne_valide(campagne)
    c = "'" + campagne + "'"
    return [
        {"nom": f"Simulation {campagne} — exécution du lanceur",
         "description": "Vrai lanceur, workers et doubles enregistrés C01 ; aucun appel applicatif ou LLM. Les incidents appartiennent au scénario synthétique.",
         "affichage": "table", "reglages": {},
         "sql": f"SELECT persona, bras, mode_execution, statut_fin, tours_joues, duree_totale_s, cout_simule_usd, cout_noa_usd AS depense_noa_usd FROM banc.v_runs WHERE campagne_id = {c} ORDER BY persona, bras"},
        {"nom": f"Simulation {campagne} — notes factices par axe",
         "description": "Deux juges déterministes, notes 1 à 4 : démonstration de chaîne, pas une évaluation de Noa.",
         "affichage": "bar", "reglages": {"graph.dimensions": ["axe", "juge"], "graph.metrics": ["moyenne"]},
         "sql": f"SELECT axe_id || ' ' || libelle AS axe, juge, version_grille, moyenne, notes, sans_objet FROM banc.v_notes_par_axe WHERE campagne_id = {c} AND passage = 1 ORDER BY axe_id, juge"},
        {"nom": f"Simulation {campagne} — incidents du scénario",
         "description": "Réponse vide, refus enregistré et artefact de banc conservés du jeu synthétique : aucun incident de production.",
         "affichage": "table", "reglages": {}, "autoriser_vide": True,
         "sql": f"SELECT type, gravite, origine, nombre, runs FROM banc.v_incidents WHERE campagne_id = {c} ORDER BY gravite, nombre DESC"},
        {"nom": f"Simulation {campagne} — démonstration alpha et kappa",
         "description": "Alpha ordinal de Krippendorff et kappa quadratique sur deux juges factices. Aucune note humaine, aucune mesure de qualité.",
         "affichage": "table", "reglages": {},
         "sql": f"SELECT 'juges factices : démonstration, pas une mesure' AS provenance, version_grille, axe_id, mesure, juges, round(valeur, 3) AS valeur, unites, motif FROM banc.v_accord WHERE campagne_id = {c} AND mesure IN ('alpha_ordinal', 'kappa_quadratique') ORDER BY mesure, axe_id"},
        {"nom": f"Simulation {campagne} — dépenses nulles",
         "description": "Dépenses de cette exécution uniquement : aucun appel API, aucun jeton facturé, 0 $. Le coût simulé est dans une autre question.",
         "affichage": "table", "reglages": {},
         "sql": f"SELECT poste, source, runs, appels, jetons, cout_usd AS depense_usd FROM banc.v_couts_par_campagne WHERE campagne_id = {c} ORDER BY poste"},
        {"nom": f"Simulation {campagne} — budget simulé séparé",
         "description": "Coût historique du scénario complet C01 utilisé par les réservations ; non proratisé si moins de tours. Ce n'est jamais une dépense de simulation.",
         "affichage": "table", "reglages": {},
         "sql": f"SELECT dialogues, cout_simule_usd, provenance FROM banc.v_couts_simules WHERE campagne_id = {c}"},
    ]


class SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def appel(methode: str, chemin: str, corps: dict | None = None, session: str | None = None):
    exiger_simulation()
    cible = urlsplit(URL)
    if cible.scheme != "http" or cible.hostname not in ("metabase", "localhost", "127.0.0.1") or cible.username or cible.password or cible.query or cible.fragment or cible.path:
        raise SystemExit("[X] URL Metabase externe ou non locale refusée")
    if not chemin.startswith("/api/") or chemin.startswith("//"):
        raise ValueError("chemin API Metabase attendu")
    donnees = json.dumps(corps).encode("utf-8") if corps is not None else None
    req = urllib.request.Request(URL + chemin, data=donnees, method=methode,
                                 headers={"Content-Type": "application/json",
                                          **({"X-Metabase-Session": session} if session else {})})
    ouvreur = urllib.request.build_opener(urllib.request.ProxyHandler({}), SansRedirection())
    try:
        with ouvreur.open(req, timeout=120) as rep:
            contenu = rep.read().decode("utf-8")
            return json.loads(contenu) if contenu else None
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"[X] Metabase {methode} {chemin} : HTTP {exc.code}") from None
    except urllib.error.URLError:
        raise SystemExit("[X] Metabase local injoignable ; vérifier le service de cette pile") from None


def session() -> str:
    proprietes = appel("GET", "/api/session/properties")
    jeton = proprietes.get("setup-token")
    if jeton and not proprietes.get("has-user-setup"):
        rep = appel("POST", "/api/setup", {
            "token": jeton,
            "user": {"first_name": "Banc", "last_name": "Noa", "email": ENV["MB_ADMIN_EMAIL"],
                     "password": ENV["MB_ADMIN_PASSWORD"], "site_name": "Banc d'essai Noa"},
            "prefs": {"site_name": "Banc d'essai Noa", "site_locale": "fr", "allow_tracking": False}})
        print("[ok] compte administrateur cree")
        return rep["id"]
    return appel("POST", "/api/session", {"username": ENV["MB_ADMIN_EMAIL"],
                                          "password": ENV["MB_ADMIN_PASSWORD"]})["id"]


def base(s: str) -> int:
    for b in appel("GET", "/api/database", session=s)["data"]:
        if b["name"] == NOM_BASE:
            return b["id"]
    b = appel("POST", "/api/database", {
        "engine": "postgres", "name": NOM_BASE,
        "details": {"host": "postgres-banc", "port": 5432, "dbname": "banc",
                    "user": "metabase_lecture", "password": ENV["METABASE_LECTURE_PASSWORD"],
                    "ssl": False, "schema-filters-type": "inclusion", "schema-filters-patterns": "banc"}},
        session=s)
    print(f"[ok] base « {NOM_BASE} » branchee (metabase_lecture, lecture seule)")
    return b["id"]


def collection(s: str) -> int:
    for c in appel("GET", "/api/collection", session=s):
        if c.get("name") == NOM_COLLECTION and not c.get("archived"):
            return c["id"]
    return appel("POST", "/api/collection", {"name": NOM_COLLECTION}, session=s)["id"]


def questions(s: str, base_id: int, coll: int, liste: list[dict]) -> list[int]:
    _RESULTATS_QUESTIONS.clear()
    existantes = {c["name"]: c["id"] for c in appel("GET", f"/api/collection/{coll}/items?models=card", session=s)["data"]}
    ids = []
    for q in liste:
        corps = {"name": q["nom"], "description": q["description"], "display": q["affichage"],
                 "visualization_settings": q["reglages"], "collection_id": coll,
                 "dataset_query": {"type": "native", "native": {"query": q["sql"]}, "database": base_id}}
        ident = existantes.get(q["nom"])
        if ident is None:
            ident = appel("POST", "/api/card", corps, session=s)["id"]
        else:
            appel("PUT", f"/api/card/{ident}", corps, session=s)
        ids.append(ident)
        resultat = appel("POST", f"/api/card/{ident}/query", {}, session=s)
        if resultat.get("error") or resultat.get("status") == "failed":
            raise SystemExit(f"[X] requête Metabase en échec : {q['nom']}")
        lignes = resultat.get("row_count")
        if lignes is None:
            lignes = len((resultat.get("data") or {}).get("rows") or [])
        if lignes <= 0 and not q.get("autoriser_vide"):
            raise SystemExit(f"[X] question Metabase vide : {q['nom']}")
        _RESULTATS_QUESTIONS.append({"id": ident, "nom": q["nom"], "lignes": lignes,
                                     "vide_autorise": bool(q.get("autoriser_vide"))})
        print(f"[ok] {q['nom']} : {lignes} ligne(s)")
    return ids


def tableau(s: str, coll: int, cartes: list[int], campagne: str) -> int:
    nom = f"Noa — simulation du lanceur ({campagne})"
    description = "Jeu synthétique C01 et doubles enregistrés ; aucun appel modèle ; deux juges factices ; dépenses nulles et budget simulé séparé."
    ident = None
    for d in appel("GET", f"/api/collection/{coll}/items?models=dashboard", session=s)["data"]:
        if d["name"] == nom:
            ident = d["id"]
    if ident is None:
        ident = appel("POST", "/api/dashboard", {"name": nom, "collection_id": coll, "description": description}, session=s)["id"]
    dashcards = [{"id": -(i + 1), "card_id": c, "row": i * 7, "col": 0, "size_x": 24, "size_y": 7}
                 for i, c in enumerate(cartes)]
    appel("PUT", f"/api/dashboard/{ident}", {"description": description, "dashcards": dashcards}, session=s)
    print(f"[ok] tableau de simulation : /dashboard/{ident}")
    return ident


def configurer(campagne: str):
    campagne_valide(campagne)
    exiger_simulation()
    for essai in range(30):
        try:
            if appel("GET", "/api/health").get("status") == "ok":
                break
        except SystemExit:
            if essai == 29:
                raise
        time.sleep(5)
    else:
        raise SystemExit("[X] Metabase n'est pas prêt après l'attente prévue")
    s = session()
    base_id = base(s)
    appel("POST", f"/api/database/{base_id}/sync_schema", {}, session=s)
    coll = collection(s)
    cartes = questions(s, base_id, coll, questions_simulation(campagne))
    ident = tableau(s, coll, cartes, campagne)
    resultat = {"campagne": campagne, "mode": "simulation", "dashboard_id": ident,
                "dashboard_path": f"/dashboard/{ident}", "questions": list(_RESULTATS_QUESTIONS)}
    dossier = chemins.RUNS / campagne
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "metabase.json").write_text(json.dumps(resultat, ensure_ascii=False, indent=2), encoding="utf-8")
    return resultat


def setup_seul():
    """Premier démarrage : compte admin et base branchés, sans carte ni donnée.
    Les questions et le tableau de bord sont créés plus tard par la tâche « metabase » du DAG."""
    for essai in range(30):
        try:
            if appel("GET", "/api/health").get("status") == "ok":
                break
        except SystemExit:
            if essai == 29:
                raise
        time.sleep(5)
    else:
        raise SystemExit("[X] Metabase n'est pas prêt après l'attente prévue")
    s = session()
    base_id = base(s)
    appel("POST", f"/api/database/{base_id}/sync_schema", {}, session=s)
    print("[ok] Metabase initialisé : compte admin et base branchés ; aucune carte créée")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default="simulation", choices=["simulation"])
    parser.add_argument("--campagne", type=campagne_valide)
    parser.add_argument("--setup-seul", action="store_true",
                        help="premier démarrage : crée le compte admin et branche la base, sans carte")
    args = parser.parse_args()
    exiger_simulation(args.mode)
    if args.setup_seul:
        setup_seul()
        return 0
    if not args.campagne:
        parser.error("--campagne requis (ou --setup-seul pour l'initialisation)")
    configurer(args.campagne)
    return 0


if __name__ == "__main__":
    sys.exit(main())

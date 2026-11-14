"""Recette des clients locaux et du rendu : faux serveur Metabase de test, pas un Metabase réel."""
from __future__ import annotations

from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlsplit, parse_qs
from unittest import mock

from banc import chemins
from entrepot import connexion, migrer, rapport
from infra import metabase_configurer as meta

RACINE = Path(__file__).resolve().parents[1]


class ServeurMetabaseFactice(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.etat = {"initialise": False, "bases": [], "collections": [], "cartes": {}, "tableaux": {},
                     "core_vide": False, "incidents_vides": False, "appels": []}
        super().__init__(("127.0.0.1", 0), Requete)


class Requete(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def repondre(self, valeur, code=200):
        octets = json.dumps(valeur).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(octets)))
        self.end_headers()
        self.wfile.write(octets)

    def traiter(self):
        etat = self.server.etat
        cible = urlsplit(self.path)
        chemin = cible.path
        contenu = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        corps = json.loads(contenu) if contenu else {}
        etat["appels"].append((self.command, chemin))
        if chemin == "/api/redirection":
            self.send_response(302)
            self.send_header("Location", "http://noa.invalid/api/session")
            self.end_headers()
            return
        if chemin == "/api/health":
            return self.repondre({"status": "ok"})
        if chemin == "/api/session/properties":
            return self.repondre({"setup-token": "initialisation-factice", "has-user-setup": etat["initialise"]})
        if chemin in ("/api/setup", "/api/session"):
            etat["initialise"] = True
            return self.repondre({"id": "session-factice"})
        if chemin == "/api/database":
            if self.command == "GET":
                return self.repondre({"data": etat["bases"]})
            etat["bases"].append({"id": 1, **corps})
            return self.repondre({"id": 1})
        if chemin.endswith("/sync_schema"):
            return self.repondre({})
        if chemin == "/api/collection":
            if self.command == "GET":
                return self.repondre(etat["collections"])
            etat["collections"].append({"id": 2, **corps})
            return self.repondre({"id": 2})
        if chemin == "/api/collection/2/items":
            modele = parse_qs(cible.query)["models"][0]
            objets = etat["cartes"] if modele == "card" else etat["tableaux"]
            return self.repondre({"data": [{"id": ident, "name": c["name"]} for ident, c in objets.items()]})
        if chemin == "/api/card":
            ident = 100 + len(etat["cartes"])
            etat["cartes"][ident] = corps
            return self.repondre({"id": ident})
        if chemin.startswith("/api/card/"):
            ident = int(chemin.split("/")[3])
            if chemin.endswith("/query"):
                nom = etat["cartes"][ident]["name"]
                vide = etat["core_vide"] or (etat["incidents_vides"] and "incidents" in nom)
                return self.repondre({"status": "completed", "row_count": 0 if vide else 2,
                                      "data": {"rows": [] if vide else [["synthétique"], ["synthétique"]]}})
            etat["cartes"][ident] = corps
            return self.repondre({"id": ident})
        if chemin == "/api/dashboard":
            ident = 500 + len(etat["tableaux"])
            etat["tableaux"][ident] = corps
            return self.repondre({"id": ident})
        if chemin.startswith("/api/dashboard/"):
            ident = int(chemin.rsplit("/", 1)[1])
            etat["tableaux"][ident].update(corps)
            return self.repondre({"id": ident})
        return self.repondre({"error": "route factice inconnue"}, 404)

    do_GET = traiter
    do_POST = traiter
    do_PUT = traiter


class MetabaseLocal(unittest.TestCase):
    def setUp(self):
        self.travail = tempfile.TemporaryDirectory(dir=RACINE / "tests")
        self.serveur = ServeurMetabaseFactice()
        self.fil = threading.Thread(target=self.serveur.serve_forever, daemon=True)
        self.fil.start()
        self.patches = [mock.patch.object(meta, "URL", f"http://127.0.0.1:{self.serveur.server_port}"),
                        mock.patch.object(chemins, "RUNS", Path(self.travail.name)),
                        mock.patch.dict(os.environ, {}, clear=True)]
        for p in self.patches:
            p.start()
        self.addCleanup(self.nettoyer)
        self.assertEqual(meta.appel("GET", "/api/health"), {"status": "ok"})

    def nettoyer(self):
        self.serveur.shutdown()
        self.serveur.server_close()
        self.fil.join(timeout=2)
        for p in reversed(self.patches):
            p.stop()
        self.travail.cleanup()

    def test_configuration_idempotente_et_questions_sans_filtre_campagnes_reelles(self):
        with redirect_stdout(StringIO()):
            premier = meta.configurer("S01")
            second = meta.configurer("S01")
        self.assertEqual(premier["dashboard_id"], second["dashboard_id"])
        self.assertEqual(len(self.serveur.etat["cartes"]), 6)
        self.assertEqual(len(self.serveur.etat["tableaux"]), 1)
        self.assertEqual(len(self.serveur.etat["tableaux"][premier["dashboard_id"]]["dashcards"]), 6)
        for q in self.serveur.etat["cartes"].values():
            self.assertIn("'S01'", q["dataset_query"]["native"]["query"])
            self.assertNotIn("v_comparaison_", q["dataset_query"]["native"]["query"])
            self.assertIn("Simulation S01", q["name"])
        base = self.serveur.etat["bases"][0]
        self.assertEqual(base["details"]["user"], "metabase_lecture")
        self.assertEqual(base["details"]["host"], "postgres-banc")
        enregistre = (Path(self.travail.name) / "S01/metabase.json").read_text(encoding="utf-8")
        self.assertNotIn("session-factice", enregistre)
        self.assertNotIn("demo-lecture-local", enregistre)

    def test_question_centrale_vide_est_un_echec(self):
        self.serveur.etat["core_vide"] = True
        with redirect_stdout(StringIO()), self.assertRaises(SystemExit):
            meta.configurer("S01")
        self.assertFalse((Path(self.travail.name) / "S01/metabase.json").exists())

    def test_absence_incident_est_valide(self):
        self.serveur.etat["incidents_vides"] = True
        with redirect_stdout(StringIO()):
            bilan = meta.configurer("S02")
        vide = [q for q in bilan["questions"] if q["lignes"] == 0]
        self.assertEqual(len(vide), 1)
        self.assertTrue(vide[0]["vide_autorise"])

    def test_redirection_et_destination_externe_refusees(self):
        with self.assertRaises(SystemExit):
            meta.appel("GET", "/api/redirection")
        with mock.patch.object(meta, "URL", "https://noa.invalid"), self.assertRaises(SystemExit):
            meta.appel("GET", "/api/health")
        self.assertFalse(any(p == "/api/session" for _, p in self.serveur.etat["appels"]))


class EntrepotEtRapport(unittest.TestCase):
    def test_dsn_ne_lit_pas_env_et_refuse_cible_non_locale(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIn("127.0.0.1", connexion.dsn())
        for url in ("postgresql://banc:x@production.invalid/banc", "postgresql://banc:x@localhost/autre",
                    "postgresql://banc:x@localhost/banc?host=production.invalid"):
            with mock.patch.dict(os.environ, {"BANC_DB_URL": url}), self.assertRaises(SystemExit):
                connexion.dsn()

    def test_migrations_versionnees_et_registre_incompatible_refuse(self):
        fichiers = migrer.fichiers()
        self.assertEqual(len(fichiers), 1)
        self.assertEqual(len(fichiers[0][2]), 16)

        class AncienneBase:
            def execute(self, requete):
                return self

            def fetchall(self):
                return [("20261011190000", "empreinte-autre-installation")]

        with self.assertRaises(SystemExit):
            migrer.etat(AncienneBase())

    def test_rendu_html_echappe_et_mentions_factices(self):
        html = rapport.tableau(["colonne"], [("<script>interdit</script>",), (None,)])
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        sections = " ".join(s[1] for s in rapport.SECTIONS)
        self.assertIn("factices", sections)
        self.assertIn("jamais une dépense", sections)


if __name__ == "__main__":
    unittest.main()

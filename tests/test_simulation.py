"""Recette locale du vrai lanceur : aucune clé, aucun client réel, aucun appel externe."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

from banc import chemins
from banc.campagnes import campagnes, parallele
from banc.dialogues import contrats, dialoguer, simulation
from banc.garde import exiger_simulation, personas_valides
from banc.juges import exporter_notation, juger
from banc.personas import controles_persona, reponses
from banc.personas.fiche_banc import FicheBanc, lire_toutes
from entrepot.calculer_accord import calculer_version
from entrepot.charger import valider_run

RACINE = Path(__file__).resolve().parents[1]
ENV_PROPRE = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "WINDIR") if k in os.environ}
ENV_PROPRE.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})


class SimulationLocale(unittest.TestCase):
    def setUp(self):
        self.travail = tempfile.TemporaryDirectory(dir=RACINE / "tests")
        self.runs = Path(self.travail.name)
        self.patches = [mock.patch.dict(os.environ, {}, clear=True),
                        mock.patch.object(dialoguer, "DOSSIER_RUNS", self.runs),
                        mock.patch.object(exporter_notation, "RUNS", self.runs),
                        mock.patch.object(juger, "RUNS", self.runs),
                        mock.patch.object(controles_persona, "RUNS", self.runs),
                        mock.patch.object(dialoguer, "LIMITE_ONBOARDING", parallele.LimiteDebit(5, 60)),
                        mock.patch.object(reponses, "LIMITE_NVIDIA", parallele.LimiteDebit(40, 60))]
        for p in self.patches:
            p.start()
        self.addCleanup(self.travail.cleanup)
        for p in self.patches:
            self.addCleanup(p.stop)
        parallele.INTERRUPTION.clear()
        contrats.definir_intervalle_noa(0)

    def lancer(self, *options):
        argv = ["dialoguer", "--campagne", "S01", "--tours", "5", "--bras", "les_deux",
                "--paralleles", "4", "--echelle-delais", "0", "--plafond-usd", "1", "--reprendre", *options]
        sortie = StringIO()
        with mock.patch.object(sys, "argv", argv), redirect_stdout(sortie):
            code = dialoguer.main()
        return code, sortie.getvalue()

    def fichiers(self):
        return sorted((self.runs / "S01").glob("S01-*.json"))

    def test_chaine_locale_et_reprise_sans_reecriture(self):
        code, journal = self.lancer()
        self.assertEqual(code, 0, journal)
        fichiers = self.fichiers()
        self.assertEqual(len(fichiers), 10)
        octets = {f.name: f.read_bytes() for f in fichiers}
        runs = [json.loads(f.read_text(encoding="utf-8")) for f in fichiers]
        for run in runs:
            valider_run(run, "S01")
            self.assertEqual(run["bras_banc"], "simulation")
            self.assertGreater(run["cout"]["simule_usd"], 0)
            self.assertEqual(run["provenance"]["mode"], "simulation")
            self.assertNotIn("app", run["cible"])
        self.assertEqual(sum(r["statut_fin"] == "TECHNICAL_ERROR" for r in runs), 1)
        self.assertEqual(sum(len(r["tours"]) for r in runs), 50)
        self.assertFalse(list((self.runs / "S01/_reservations").glob("*.json")))
        self.assertFalse(simulation.GARDE_RESEAU["bloquees"])
        self.assertFalse(simulation.GARDE_RESEAU["active"])
        code, journal = self.lancer()
        self.assertEqual(code, 0, journal)
        self.assertEqual(octets, {f.name: f.read_bytes() for f in self.fichiers()})
        self.assertIn("0 a faire", journal)
        self.assertEqual(len(controles_persona.controler("S01")["runs"]), 10)
        with mock.patch.object(sys, "argv", ["exporter", "--campagne", "S01"]), redirect_stdout(StringIO()):
            self.assertEqual(exporter_notation.main(), 0)
        corr = json.loads((self.runs / "S01/notation/correspondance.json").read_text(encoding="utf-8"))
        self.assertEqual(len(corr["dialogues"]), 9)
        for _ in range(2):
            with mock.patch.object(sys, "argv", ["juger", "--campagne", "S01", "--paralleles", "4"]), redirect_stdout(StringIO()):
                self.assertEqual(juger.main(), 0)
        lignes = []
        sorties = sorted((self.runs / "S01/juges").glob("*/*.json"))
        self.assertEqual(len(sorties), 18)
        for f in sorties:
            note = json.loads(f.read_text(encoding="utf-8"))
            self.assertEqual(note["usage"]["essais_factures"], 0)
            self.assertEqual(note["cout_usd"], 0)
            for axe in juger.CRITERES:
                lignes.append((note["fichier_run"], axe, note["juge"], note["passage"], note["notes"][axe]))
        mesures = []
        calculer_version(lambda *args: mesures.append(args), lignes)
        for nom in ("alpha_ordinal", "kappa_quadratique"):
            self.assertTrue(any(m[1] == nom and m[3] is not None for m in mesures))

    def test_plafond_simule_arrete_avant_demarrage(self):
        # Le vrai plafond voit dix réservations de 0,04, pas des dépenses nulles.
        with mock.patch.object(sys, "argv", ["dialoguer", "--campagne", "S01", "--plafond-usd", "0.1",
                                               "--cout-estime-dialogue", "0.04"]), redirect_stdout(StringIO()):
            self.assertEqual(dialoguer.main(), 3)
        self.assertFalse(self.fichiers())

    def test_isolement_d_erreur_et_reprise(self):
        original = dialoguer.dialoguer

        def echouer(fiche, *args, **kwargs):
            if fiche.identifiant == "jeanpierre":
                raise RuntimeError("panne locale simulée")
            return original(fiche, *args, **kwargs)

        options = ["--personas", "claire,jeanpierre", "--bras", "sans_onboarding"]
        with mock.patch.object(dialoguer, "dialoguer", echouer):
            code, journal = self.lancer(*options)
        self.assertEqual(code, 2, journal)
        self.assertEqual(len(self.fichiers()), 1)
        self.assertTrue((self.runs / "S01/_echecs.json").exists())
        self.assertTrue(list((self.runs / "S01/_reservations").glob("*.json")))
        code, journal = self.lancer(*options)
        self.assertEqual(code, 0, journal)
        self.assertEqual(len(self.fichiers()), 2)

    def test_marques_du_jeu_et_couts_conserves_separement(self):
        self.lancer()
        runs = [json.loads(f.read_text(encoding="utf-8")) for f in self.fichiers()]
        lucas = next(r for r in runs if r["persona"]["identifiant"] == "lucas" and r["bras"] == "sans_onboarding")
        self.assertIn("hors notation", lucas["marquage"])
        self.assertAlmostEqual(sum(r["cout"]["simule_usd"] for r in runs), 0.177619)
        self.assertEqual(sum(r["cout"][p]["cout_usd"] for r in runs for p in ("dialogue", "onboarding", "analyse")), 0)

    def test_garde_absente_refuse_dialogue_direct(self):
        with self.assertRaises(PermissionError):
            dialoguer.dialoguer(lire_toutes()[0], "20261114T000000Z", 1, "S01", "sans_onboarding")

    def test_reservation_illisible_reste_prudente_et_pas_double_compte(self):
        d = self.runs / "S01/_reservations"
        d.mkdir(parents=True)
        f = d / "S01-20261114T000000Z-claire-sans_onboarding.json"
        f.write_text("{tronqué", encoding="utf-8")
        with redirect_stdout(StringIO()):
            total, n = dialoguer.depense_campagne("S01")
        self.assertEqual((total, n), (dialoguer.RESERVATION_ILLISIBLE_USD, 0))
        run = {"cout": {"simule_usd": 0.01}}
        (d.parent / f.name).write_text(json.dumps(run), encoding="utf-8")
        self.assertEqual(dialoguer.depense_campagne("S01"), (0.01, 1))


class GardeReseau(unittest.TestCase):
    def test_tcp_udp_dns_urllib_sous_processus_refuses(self):
        tcp = socket.socket()
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(tcp.close)
        self.addCleanup(udp.close)
        operations = [lambda: socket.socket(), lambda: tcp.connect(("127.0.0.1", 9)),
                      lambda: udp.sendto(b"aucun-envoi", ("127.0.0.1", 9)),
                      lambda: socket.getaddrinfo("noa.invalid", 443),
                      lambda: urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://noa.invalid"),
                      lambda: subprocess.run([sys.executable, "-c", "pass"], check=True),
                      lambda: sys.audit("os.fork"), lambda: os.system("false")]
        with mock.patch.dict(os.environ, {"AIRFLOW__METRICS__STATSD_HOST": "127.0.0.1"}), simulation.reseau_interdit():
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaises(PermissionError):
                    operation()
        self.assertEqual(len(simulation.GARDE_RESEAU["bloquees"]), len(operations))

    def test_garde_propagee_aux_workers_et_contextes_imbriques(self):
        bloques = []

        def essai():
            try:
                socket.getaddrinfo("noa.invalid", 443)
            except PermissionError:
                bloques.append(True)

        with simulation.reseau_interdit():
            with simulation.reseau_interdit():
                pass
            self.assertTrue(simulation.GARDE_RESEAU["active"])
            fils = [threading.Thread(target=essai) for _ in range(4)]
            for fil in fils:
                fil.start()
            for fil in fils:
                fil.join()
        self.assertEqual(len(bloques), 4)
        self.assertFalse(simulation.GARDE_RESEAU["active"])

    def test_installation_interdite_sans_garde_et_empreinte_controlee(self):
        with self.assertRaises(PermissionError):
            simulation.installer(dialoguer, chemins.JEUX_DEMO / "C01", 0)
        originale = Path.read_bytes
        cible = next((chemins.JEUX_DEMO / "C01").glob("C01-*.json"))

        def corrompre(path):
            return b"{}" if path == cible else originale(path)

        with simulation.reseau_interdit(), mock.patch.object(Path, "read_bytes", corrompre), self.assertRaises(ValueError):
            simulation.installer(dialoguer, chemins.JEUX_DEMO / "C01", 0)

    def test_metriques_n_emettent_meme_si_variable_statsd_presente(self):
        with mock.patch.dict(os.environ, {"AIRFLOW__METRICS__STATSD_HOST": "127.0.0.1"}), \
                mock.patch.object(socket, "socket", side_effect=AssertionError("réseau interdit")):
            parallele.envoyer_jauge("simulation.test", 1)


class ContratsEtConfiguration(unittest.TestCase):
    def test_imports_frais_sans_cles_et_sans_lecture_env(self):
        code = '''import io, pathlib, sys
original = io.open
def protege(file, *a, **k):
    if pathlib.Path(file).name == '.env' or pathlib.Path(file).name.startswith('.env.'):
        raise AssertionError('lecture de fichier environnement')
    return original(file, *a, **k)
io.open = protege
from banc.dialogues import dialoguer, simulation
from banc.juges import juger
from banc.personas.fiche_banc import lire_toutes
assert len(lire_toutes()) == 5
for nom in ('openai', 'google.genai', 'pydantic', 'banc.dialogues.client_noa', 'banc.personas.moteur_persona'):
    assert nom not in sys.modules, nom
print('imports propres')
'''
        fin = subprocess.run([sys.executable, "-c", code], cwd=RACINE, env=ENV_PROPRE, capture_output=True, text=True)
        self.assertEqual(fin.returncode, 0, fin.stderr)

    def test_modes_cli_non_simules_et_anciens_points_entree_refuses(self):
        commandes = [["-m", "banc.dialogues.dialoguer", "--campagne", "S01", "--mode", "test_fixe"],
                     ["-m", "banc.dialogues.dialoguer", "--campagne", "C111"],
                     ["-m", "banc.dialogues.dialoguer", "--campagne", "S01", "--simulation", "."],
                     ["-m", "banc.dialogues.dialoguer", "--campagne", "S01", "--echelle-delais", "nan"],
                     ["-m", "entrepot.preparer", "--campagne", "S01", "--mode", "reel"],
                     ["-m", "banc.juges.juger", "--campagne", "S01", "--juges", "gemini"],
                     ["-m", "infra.metabase_configurer", "--campagne", "S01", "--mode", "reel"],
                     ["-m", "banc.campagnes.campagnes", "--verifier", "S01", "reel"]]
        for arguments in commandes:
            with self.subTest(arguments=arguments):
                fin = subprocess.run([sys.executable, *arguments], cwd=RACINE, env=ENV_PROPRE,
                                     text=True, capture_output=True, timeout=20)
                self.assertNotEqual(fin.returncode, 0, fin.stdout)
                self.assertNotIn("ModuleNotFoundError", fin.stderr)

    def test_fiches_et_contraintes_validees(self):
        fiches = lire_toutes()
        self.assertEqual(len(fiches), 5)
        original = fiches[0].model_dump()
        for chemin, valeur in ((["axes", "mineur"], True), (["fiche", "identite", "age"], 12),
                               (["axes", "style_ecriture"], "inconnu"), (["identifiant"], "../interdit"),
                               (["contraintes"], ["contrainte absente du rôle"])):
            copie = json.loads(json.dumps(original))
            courant = copie
            for cle in chemin[:-1]:
                courant = courant[cle]
            courant[chemin[-1]] = valeur
            with self.subTest(chemin=chemin), self.assertRaises(ValueError):
                FicheBanc.model_validate(copie)
        with self.assertRaises(ValueError):
            personas_valides("../interdit")

    def test_nomenclature_et_mode_environnement(self):
        for code in ("S01", "S99", "S100"):
            self.assertIsNone(campagnes.refus_lancement(code, "simulation"))
        for code in ("S00", "S001", "C01", "C111", "../S01", "S01\n"):
            self.assertIsNotNone(campagnes.refus_lancement(code, "simulation"))
        with mock.patch.dict(os.environ, {"BANC_MODE": "reel"}), self.assertRaises(SystemExit):
            exiger_simulation()

    def test_file_analyse_locale_fermee_et_intervalle_partage(self):
        file = simulation.N8nSimule(0.001)
        try:
            file.mettre_en_attente("a")
            file.mettre_en_attente("b")
            self.assertEqual(file.attendre("a"), "done")
            self.assertEqual(file.attendre("b"), "done")
        finally:
            file.fermer()
        self.assertFalse(file._fil.is_alive())
        parallele.INTERRUPTION.clear()
        contrats.definir_intervalle_noa(0.02)
        instants = []
        verrou = threading.Lock()

        def worker():
            contrats.espacer_appel_noa()
            with verrou:
                instants.append(time.monotonic())

        try:
            fils = [threading.Thread(target=worker) for _ in range(4)]
            for fil in fils:
                fil.start()
            for fil in fils:
                fil.join()
            instants.sort()
            self.assertEqual(len(instants), 4)
            self.assertTrue(all(b - a >= 0.012 for a, b in zip(instants, instants[1:])))
        finally:
            contrats.definir_intervalle_noa(0)


if __name__ == "__main__":
    unittest.main()

"""Tests d'ordonnancement repris de la branche source et adaptés à la distribution simulée."""

from __future__ import annotations
import json
import os
import random
import socket
import sys
import tempfile
import threading
import time
import unittest
from collections import Counter
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from banc.dialogues import dialoguer
from banc.juges import juger
from banc.personas import reponses as moteur_persona
from banc.campagnes import parallele
from banc.campagnes.parallele import LimiteDebit, Pause429, Plafond, executer

RACINE = Path(__file__).resolve().parents[1]


class Horloge:
    """Horloge factice : dormir() avance le temps, sans attendre."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.attentes: list[float] = []

    def __call__(self) -> float:
        return self.t

    def dormir(self, s: float) -> None:
        self.attentes.append(round(s, 3))


class TestLimiteDebit(unittest.TestCase):
    def test_fenetre_glissante(self) -> None:
        h = Horloge()
        limite = LimiteDebit(5, 60.0, horloge=h, dormir=h.dormir)
        attentes = [limite.attendre() for _ in range(7)]  # 7 appels arrives en meme temps
        self.assertEqual(attentes, [0, 0, 0, 0, 0, 60.0, 60.0])
        h.t += 61  # une minute plus tard : 5 creneaux liberes
        self.assertEqual(limite.attendre(), 0)

    def test_une_file_commune_a_tous_les_fils(self) -> None:
        # 4 fils x 4 appels sur une limite de 4 par 0,2 s : jamais plus de 4 appels dans une fenetre de 0,2 s
        limite, instants, verrou = LimiteDebit(4, 0.2), [], threading.Lock()

        def worker() -> None:
            for _ in range(4):
                limite.attendre()
                with verrou:
                    instants.append(time.monotonic())

        fils = [threading.Thread(target=worker) for _ in range(4)]
        debut = time.monotonic()
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        instants.sort()
        self.assertEqual(len(instants), 16)
        for a, b in zip(instants, instants[4:]):
            self.assertGreaterEqual(b - a, 0.2 - 0.02)
        self.assertGreaterEqual(time.monotonic() - debut, 0.6 - 0.05)  # (16 / 4 - 1) x 0,2 s au moins

    def test_limites_du_banc(self) -> None:
        self.assertEqual((dialoguer.LIMITE_ONBOARDING.nombre, dialoguer.LIMITE_ONBOARDING.periode_s), (5, 60.0))
        self.assertEqual((moteur_persona.LIMITE_NVIDIA.nombre, moteur_persona.LIMITE_NVIDIA.periode_s), (40, 60.0))


class TestPause429(unittest.TestCase):
    def test_attente_doublee_et_plafonnee(self) -> None:
        h = Horloge()
        pause = Pause429(base_s=10, plafond_s=60, horloge=h, dormir=h.dormir)
        attentes = []
        for _ in range(5):
            attentes.append(pause.signaler())
            h.t += attentes[-1]
        self.assertEqual(attentes, [10, 20, 40, 60, 60])
        pause.succes()
        self.assertEqual(pause.signaler(), 10)

    def test_pause_commune_aux_workers(self) -> None:
        # un worker prend un 429 : les autres attendent aussi avant leur prochain appel
        pause = Pause429(base_s=0.3, plafond_s=1.0)
        pause.signaler()
        attentes: list[float] = []
        fils = [threading.Thread(target=lambda: attentes.append(pause.attendre())) for _ in range(3)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        self.assertEqual(len(attentes), 3)
        self.assertTrue(all(a > 0.2 for a in attentes), attentes)


class TestPlafond(unittest.TestCase):
    def test_reservation_puis_cout_reel(self) -> None:
        p = Plafond(1.0, Counter({"noa": 3}), estimations={"noa": 0.30})
        self.assertEqual(p.reserver("noa"), (0.30, None))  # projection 0,90 <= 1,00
        p.liberer("noa", 0.30, 0.20)  # cout reel 0,20 : moyenne 0,20, estimation 0,30
        self.assertEqual(p.reserver("noa")[0], 0.30)  # l'estimation fournie reste un plancher
        montant, motif = p.reserver("noa")
        self.assertIsNotNone(montant)
        self.assertAlmostEqual(p.depense + p.reserve, 0.80)

    def test_rien_ne_demarre_au_dela(self) -> None:
        p = Plafond(1.0, Counter({"noa": 4}), estimations={"noa": 0.30})
        montant, motif = p.reserver("noa")  # 4 x 0,30 = 1,20 > 1,00
        self.assertIsNone(montant)
        self.assertIn("plafond 1.0 $", motif)

    def test_cout_inconnu_compte_a_la_reservation(self) -> None:
        p = Plafond(1.0, Counter({"noa": 2}), estimations={"noa": 0.25})
        montant, _ = p.reserver("noa")
        p.liberer("noa", montant, None)  # dialogue en erreur : jamais compte 0
        self.assertAlmostEqual(p.depense, 0.25)

    def test_sans_estimation_regle_sequentielle(self) -> None:
        # sans estimation fournie : cout moyen mesure (regle sequentielle de dialoguer.py)
        p = Plafond(1.0, Counter({"noa": 3}), deja={"noa": (0.40, 1)})
        montant, motif = p.reserver("noa")  # 0,40 + 3 x 0,40 > 1,00
        self.assertIsNone(montant)
        self.assertIn("projection", motif)

    def test_postes_separes(self) -> None:
        p = Plafond(0.10, Counter({"poste-a": 1, "factice": 5}), estimations={"poste-a": 0.05})
        self.assertEqual(p.reserver("factice"), (0.0, None))
        self.assertEqual(p.reserver("poste-a"), (0.05, None))


class TestExecuteur(unittest.TestCase):
    def test_ordre_erreurs_et_journal_par_worker(self) -> None:
        def faire(n: int) -> int:
            time.sleep(random.Random(n).uniform(0, 0.02))
            print(f"tache {n} : ", end="")
            print("debut")
            if n == 3:
                raise ValueError("tache 3 en erreur")
            return n * 10

        fils_terminer: set[str] = set()
        sortie = StringIO()
        with redirect_stdout(sortie):
            resultats, arret = executer(
                list(range(12)), 4, faire, terminer=lambda r, v, e: fils_terminer.add(threading.current_thread().name)
            )
        self.assertIsNone(arret)
        self.assertEqual([r for r, _, _ in resultats], list(range(12)))
        self.assertEqual([v for _, v, e in resultats if e is None], [n * 10 for n in range(12) if n != 3])
        self.assertIsInstance(resultats[3][2], ValueError)
        self.assertEqual(fils_terminer, {"MainThread"})  # comptes tenus dans le fil principal
        lignes = sortie.getvalue().splitlines()
        self.assertEqual(len(lignes), 12)
        for ligne in lignes:
            self.assertRegex(ligne, r"^\[w[1-4]\] tache \d+ : debut$")

    def test_arret_et_interruption(self) -> None:
        demarres: list[int] = []
        with redirect_stdout(StringIO()):
            resultats, arret = executer(
                list(range(10)), 4, demarres.append, autoriser=lambda r: "assez" if r == 6 else None
            )
        self.assertEqual((arret, len(resultats), sorted(demarres)), ("assez", 6, list(range(6))))

        def garde(n: int) -> None:
            raise SystemExit("[X] garde de production")

        with self.assertRaises(SystemExit), redirect_stdout(StringIO()):
            executer([1, 2], 2, garde)
        with self.assertRaises(ValueError):
            executer([1], 5, print)


class TestVerrouDuLanceur(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(dir=RACINE / "tests")
        self.verrou = Path(self.tmp.name) / "S193" / "_lanceur-dialogues.lock"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def ecrire(self, pid: int, machine: str | None = None) -> None:
        self.verrou.parent.mkdir(parents=True, exist_ok=True)
        self.verrou.write_text(
            json.dumps(
                {"pid": pid, "machine": machine or socket.gethostname(), "depuis_utc": "2026-10-17T12:00:00+00:00"}
            ),
            encoding="utf-8",
        )

    def test_un_seul_lanceur_par_campagne(self) -> None:
        with parallele.verrou_lanceur(self.verrou):
            self.assertTrue(self.verrou.exists())
            with self.assertRaises(SystemExit) as e:  # un second lanceur, PID vivant
                with parallele.verrou_lanceur(self.verrou):
                    pass
            self.assertIn("un lanceur tourne deja", str(e.exception))
        self.assertFalse(self.verrou.exists())  # retire a la sortie normale
        with self.assertRaises(ValueError):
            with parallele.verrou_lanceur(self.verrou):
                raise ValueError("erreur du lanceur")
        self.assertFalse(self.verrou.exists())  # et apres une erreur

    def test_verrou_perime_ou_d_une_autre_machine(self) -> None:
        import subprocess

        mort = subprocess.Popen([sys.executable, "-c", "pass"])
        mort.wait()
        self.ecrire(mort.pid)
        self.assertFalse(parallele.processus_vivant(mort.pid))
        with redirect_stdout(StringIO()) as sortie, parallele.verrou_lanceur(self.verrou):
            self.assertEqual(json.loads(self.verrou.read_text(encoding="utf-8"))["pid"], os.getpid())
        self.assertIn("verrou perime", sortie.getvalue())
        self.ecrire(mort.pid, machine="autre-machine")  # PID invérifiable : refus
        with self.assertRaises(SystemExit):
            with parallele.verrou_lanceur(self.verrou):
                pass
        self.verrou.write_text("{tronque", encoding="utf-8")
        with self.assertRaises(SystemExit) as e:
            with parallele.verrou_lanceur(self.verrou):
                pass
        self.assertIn("illisible", str(e.exception))

    def test_dialoguer_et_juger_refusent_un_second_lanceur(self) -> None:
        self.ecrire(os.getpid())
        with (
            mock.patch.object(dialoguer, "DOSSIER_RUNS", Path(self.tmp.name)),
            mock.patch.object(sys, "argv", ["dialoguer.py", "--campagne", "S193"]),
            self.assertRaises(SystemExit),
        ):
            dialoguer.main()
        verrou_juges = Path(self.tmp.name) / "S193" / "_lanceur-juges.lock"
        verrou_juges.write_text(self.verrou.read_text(encoding="utf-8"), encoding="utf-8")
        with (
            mock.patch.object(juger, "RUNS", Path(self.tmp.name)),
            mock.patch.object(sys, "argv", ["juger.py", "--campagne", "S193"]),
            self.assertRaises(SystemExit) as e,
        ):
            juger.main()
        self.assertIn("un lanceur tourne deja", str(e.exception))

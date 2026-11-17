"""
Controles Python du persona (controles_persona.py) : longueur sur tout le dialogue comparee a la fiche,
statut de fin (derive pour les runs anciens qui ne l'enregistrent pas), secret revele des le tour 1.
Sur un run v2 construit a la main et sur les runs de demo/C01. Aucun appel reseau.

Lancer : python -m unittest tests.test_controles_persona -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from banc.personas import controles_persona as cp  # noqa: E402


def run_v2() -> dict:
    return {
        "type_run": "dialogue",
        "version_persona": "v2",
        "bras": "sans_onboarding",
        "statut_fin": "QUIT_NEGATIVE",
        "motif_fin": "trop de questions",
        "tour_fin": 3,
        "depart": {"statut": "QUIT_NEGATIVE", "decision_forme": "toleree"},
        "persona": {
            "identifiant": "claire",
            "axes": {"style_ecriture": "telegraphique"},
            "longueur_moyenne_mots": 10,
            "ne_dira_pas_sans_confiance": ["qu'elle ne dort plus depuis l'enterrement"],
        },
        "modele_persona": {"plafond_mots": 25},
        "resume": {"tours_demandes": 5},
        "tours": [
            {"n": 1, "persona": "je dors plus depuis l'enterrement de mon pere", "noa": "Je suis désolé."},
            {"n": 2, "persona": "ok", "noa": "Tu veux en parler ?", "persona_decision_forme": "stricte"},
            {
                "n": 3,
                "persona": "jsp. le boulot surtout, les horaires de nuit qui me cassent, le bus, tout ca",
                "noa": "Qu'est-ce qui pèse le plus ?",
                "persona_decision_forme": "repli",
            },
        ],
    }


class TestControlesPersona(unittest.TestCase):
    def test_run_v2(self):
        c = cp.controler_run(run_v2(), "C93-x-claire-sans_onboarding.json")
        lg, fin, sec = c["longueur"], c["fin"], c["secret"]
        self.assertEqual((lg["messages_ecrits"], lg["maximum_mots"], lg["part_courtes"]), (2, 15, 0.5))
        self.assertEqual((lg["moyenne_mots"], lg["attendue_mots"], lg["source_attendue"]), (8.0, 10, "run"))
        self.assertFalse(lg["trop_long"])
        self.assertEqual((fin["statut_fin"], fin["statut_derive"], fin["tour_fin"]), ("QUIT_NEGATIVE", False, 3))
        self.assertEqual((fin["decisions_stricte"], fin["decisions_toleree"], fin["decisions_repli"]), (1, 1, 1))
        self.assertEqual((fin["decisions_relance"], fin["relances"]), (0, 0))
        self.assertTrue(sec["revele_tour_1"])
        self.assertEqual(sec["premier_tour_affleure"], 1)

    def test_relances(self):
        run = run_v2()
        run["tours"][2].update(persona_decision_forme="relance", persona_decision_relance=True)
        run["depart"].update(decision_forme="repli", decision_relance=True)
        fin = cp.controler_run(run, "x.json")["fin"]
        self.assertEqual(
            (fin["decisions_stricte"], fin["decisions_relance"], fin["decisions_repli"], fin["relances"]), (1, 1, 1, 2)
        )

    def test_secret(self):
        s = "qu'il a menti à ses parents sur ses résultats du premier semestre"
        self.assertTrue(cp.secret_present(s, "j'ai menti a mes parents pour mes resultats"))
        self.assertFalse(cp.secret_present(s, "les exams arrivent et je panique"))
        self.assertFalse(cp.secret_present("", "peu importe"))

    def test_runs_historiques_et_fichier(self):
        with tempfile.TemporaryDirectory(dir=RACINE / "tests") as tmp:
            dossier = Path(tmp) / "S91"
            dossier.mkdir()
            for f in sorted((RACINE / "demo" / "C01").glob("C01-*.json")):
                shutil.copy(f, dossier / f.name.replace("C01-", "S91-"))
            (dossier / "S91-20261016T000000Z-tf-TF01-bras-1.json").write_text(
                json.dumps({"type_run": "test_fixe", "persona": {"identifiant": "test_fixe"}, "tours": []}),
                encoding="utf-8",
            )
            r = cp.controler("S91", Path(tmp))
            self.assertEqual((len(r["runs"]), r["runs_ignores"]), (10, 1))  # le test a entree fixe est ignore
            for c in r["runs"]:
                self.assertEqual(c["version_persona"], "v1")
                self.assertTrue(c["fin"]["statut_derive"])
                self.assertIn(c["fin"]["statut_fin"], ("MAX_TURNS", "TECHNICAL_ERROR"))
            ecrit = json.loads((dossier / "controles" / "persona.json").read_text(encoding="utf-8"))
            self.assertEqual(len(ecrit["runs"]), 10)


if __name__ == "__main__":
    unittest.main()

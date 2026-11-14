"""Tests de accord.py sur des donnees fictives aux valeurs calculees a la main.

Lancer : python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from banc.notation.accord import alpha_krippendorff, kappa_pondere  # noqa: E402


class AlphaKrippendorff(unittest.TestCase):
    def test_accord_parfait_vaut_1(self):
        d = {u: {"a": v, "b": v, "c": v} for u, v in enumerate([1, 2, 3, 4, 2, 3])}
        for niveau in ("nominal", "ordinal", "intervalle"):
            self.assertAlmostEqual(alpha_krippendorff(d, niveau), 1.0)

    def test_nominal_calcule_a_la_main(self):
        # Unites (1,1), (2,2), (1,2) : o11=2, o22=2, o12=o21=1, n=6, n1=n2=3.
        # D_obs = 2/6 ; D_att = 2*3*3/(6*5) = 0,6 ; alpha = 1 - (1/3)/0,6 = 4/9.
        d = {1: {"a": 1, "b": 1}, 2: {"a": 2, "b": 2}, 3: {"a": 1, "b": 2}}
        self.assertAlmostEqual(alpha_krippendorff(d, "nominal"), 4 / 9)

    def test_intervalle_calcule_a_la_main(self):
        # Memes unites, distance (c-k)^2 = 1 pour 1/2 : memes D_obs et D_att qu'en nominal.
        d = {1: {"a": 1, "b": 1}, 2: {"a": 2, "b": 2}, 3: {"a": 1, "b": 2}}
        self.assertAlmostEqual(alpha_krippendorff(d, "intervalle"), 4 / 9)

    def test_ordinal_penalise_plus_un_ecart_de_trois_crans(self):
        proche = {1: {"a": 1, "b": 1}, 2: {"a": 4, "b": 4}, 3: {"a": 2, "b": 3}, 4: {"a": 3, "b": 3}}
        loin = {1: {"a": 1, "b": 1}, 2: {"a": 4, "b": 4}, 3: {"a": 1, "b": 4}, 4: {"a": 3, "b": 3}}
        self.assertGreater(alpha_krippendorff(proche, "ordinal"), alpha_krippendorff(loin, "ordinal"))

    def test_desaccord_systematique_est_negatif(self):
        d = {u: {"a": 1, "b": 4} if u % 2 else {"a": 4, "b": 1} for u in range(6)}
        self.assertLess(alpha_krippendorff(d, "ordinal"), 0)

    def test_valeurs_manquantes_et_unites_isolees(self):
        # B6 « sans objet » (None) et une unite notee par un seul juge : ignorees.
        avec = {
            1: {"a": 1, "b": 1},
            2: {"a": 2, "b": 2},
            3: {"a": 1, "b": 2},
            4: {"a": None, "b": None},
            5: {"a": 3, "b": None},
        }
        sans = {1: {"a": 1, "b": 1}, 2: {"a": 2, "b": 2}, 3: {"a": 1, "b": 2}}
        self.assertAlmostEqual(alpha_krippendorff(avec, "nominal"), alpha_krippendorff(sans, "nominal"))

    def test_indefini_si_une_seule_valeur(self):
        self.assertIsNone(alpha_krippendorff({1: {"a": 2, "b": 2}, 2: {"a": 2, "b": 2}}))
        self.assertIsNone(alpha_krippendorff({1: {"a": 2}}))


class KappaPondere(unittest.TestCase):
    def test_deux_categories_egal_kappa_de_cohen(self):
        # A = 1,1,2 ; B = 1,2,2 : p_o = 2/3, p_e = 4/9, kappa = (2/3-4/9)/(1-4/9) = 0,4.
        for poids in ("quadratique", "lineaire"):
            self.assertAlmostEqual(kappa_pondere([1, 1, 2], [1, 2, 2], [1, 2], poids), 0.4)

    def test_accord_parfait_vaut_1(self):
        self.assertAlmostEqual(kappa_pondere([1, 2, 3, 4], [1, 2, 3, 4], [1, 2, 3, 4]), 1.0)

    def test_quadratique_pardonne_plus_les_petits_ecarts(self):
        a, b = [1, 2, 3, 4, 2, 3], [2, 2, 4, 4, 1, 3]
        self.assertGreater(
            kappa_pondere(a, b, [1, 2, 3, 4], "quadratique"), kappa_pondere(a, b, [1, 2, 3, 4], "lineaire")
        )

    def test_refuse_les_manques(self):
        with self.assertRaises(ValueError):
            kappa_pondere([1, None], [1, 2], [1, 2])

    def test_proche_de_alpha_intervalle_sur_grand_echantillon(self):
        # Pour deux annotateurs sans manque, kappa quadratique et alpha intervalle convergent.
        import random

        rnd = random.Random(7)
        a = [rnd.randint(1, 4) for _ in range(400)]
        b = [max(1, min(4, x + rnd.choice((-1, 0, 0, 1)))) for x in a]
        d = {i: {"a": x, "b": y} for i, (x, y) in enumerate(zip(a, b))}
        self.assertAlmostEqual(kappa_pondere(a, b, [1, 2, 3, 4]), alpha_krippendorff(d, "intervalle"), delta=0.01)


if __name__ == "__main__":
    unittest.main()

"""
Accord entre annotateurs (juges LLM, juge humain) sur les notes de la grille.

Deux mesures, sans dependance externe :
  - alpha de Krippendorff, niveaux nominal, ordinal ou intervalle. Accepte plus de deux
    annotateurs et les valeurs manquantes (un juge qui n'a pas note, B6 « sans objet »).
    Mesure retenue pour le banc : niveau ORDINAL, les notes 1 a 4 etant ordonnees sans
    que les ecarts soient egaux.
  - kappa de Cohen pondere (poids lineaires ou quadratiques), pour deux annotateurs
    seulement et sans valeur manquante. Sert de recoupement, et de comparaison avec la
    litterature qui l'emploie.

Format des donnees : {unite: {annotateur: valeur ou None}}. Une unite est par exemple
« run N05, axe B1 ». Une unite notee par un seul annotateur ne compte pas (rien a
comparer).

Reference : K. Krippendorff, « Computing Krippendorff's Alpha-Reliability », 2011.

Usage : python -m banc.notation.accord --demo   (calcule les deux mesures sur des donnees fictives)
"""
from __future__ import annotations

import sys
from collections import Counter
from itertools import permutations


def _coincidences(donnees: dict) -> tuple[dict, Counter, float]:
    """Matrice de coincidences o[(c, k)], marges n_c, total n (Krippendorff 2011, §2)."""
    o: Counter = Counter()
    for notes in donnees.values():
        valeurs = [v for v in notes.values() if v is not None]
        m = len(valeurs)
        if m < 2:
            continue
        for c, k in permutations(valeurs, 2):
            o[(c, k)] += 1 / (m - 1)
    marges: Counter = Counter()
    for (c, _k), poids in o.items():
        marges[c] += poids
    return o, marges, sum(marges.values())


def _distance2(c, k, niveau: str, marges: Counter, valeurs: list) -> float:
    if niveau == "nominal":
        return 0.0 if c == k else 1.0
    if niveau == "intervalle":
        return float(c - k) ** 2
    if niveau == "ordinal":
        if c == k:
            return 0.0
        bas, haut = sorted((c, k))
        entre = sum(marges[g] for g in valeurs if bas <= g <= haut)
        return (entre - (marges[bas] + marges[haut]) / 2) ** 2
    raise ValueError(f"niveau inconnu : {niveau}")


def alpha_krippendorff(donnees: dict, niveau: str = "ordinal") -> float | None:
    """Alpha de Krippendorff. None si aucune paire n'est comparable ou si toutes les
    valeurs sont identiques (desaccord attendu nul : alpha indefini)."""
    o, marges, n = _coincidences(donnees)
    if n <= 1:
        return None
    valeurs = sorted(marges)
    d_obs = sum(poids * _distance2(c, k, niveau, marges, valeurs) for (c, k), poids in o.items()) / n
    d_att = sum(marges[c] * marges[k] * _distance2(c, k, niveau, marges, valeurs)
                for c in valeurs for k in valeurs) / (n * (n - 1))
    if d_att == 0:
        return None
    return 1 - d_obs / d_att


def kappa_pondere(a: list, b: list, categories: list, poids: str = "quadratique") -> float | None:
    """Kappa de Cohen pondere entre deux annotateurs, sur des listes alignees sans manque."""
    if len(a) != len(b) or not a:
        raise ValueError("listes vides ou de longueurs differentes")
    if any(v is None for v in (*a, *b)):
        raise ValueError("valeur manquante : utiliser alpha_krippendorff")
    idx = {c: i for i, c in enumerate(categories)}
    q = len(categories)
    if q < 2:
        return None

    def w(i: int, j: int) -> float:  # poids de DESACCORD, 0 sur la diagonale
        d = abs(i - j) / (q - 1)
        return d ** 2 if poids == "quadratique" else d

    n = len(a)
    obs = Counter((idx[x], idx[y]) for x, y in zip(a, b))
    pa = Counter(idx[x] for x in a)
    pb = Counter(idx[y] for y in b)
    d_obs = sum(w(i, j) * c for (i, j), c in obs.items()) / n
    d_att = sum(w(i, j) * pa[i] * pb[j] for i in range(q) for j in range(q)) / (n * n)
    if d_att == 0:
        return None
    return 1 - d_obs / d_att


def _demo() -> None:
    # Deux juges fictifs sur 6 runs x 5 axes, notes 1 a 4, un desaccord d'un cran par run.
    import random
    rnd = random.Random(20261011)
    donnees, a, b = {}, [], []
    for run in range(1, 7):
        for axe in ("B1", "B2", "B3", "B4", "B5"):
            v = rnd.randint(1, 4)
            w = v if (run + len(axe)) % 3 else max(1, min(4, v + rnd.choice((-1, 1))))
            donnees[(f"N0{run}", axe)] = {"juge_a": v, "juge_b": w}
            a.append(v)
            b.append(w)
    print(f"alpha ordinal      : {alpha_krippendorff(donnees, 'ordinal'):.3f}")
    print(f"alpha intervalle   : {alpha_krippendorff(donnees, 'intervalle'):.3f}")
    print(f"alpha nominal      : {alpha_krippendorff(donnees, 'nominal'):.3f}")
    print(f"kappa quadratique  : {kappa_pondere(a, b, [1, 2, 3, 4]):.3f}")
    print(f"kappa lineaire     : {kappa_pondere(a, b, [1, 2, 3, 4], 'lineaire'):.3f}")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        _demo()
    else:
        print(__doc__)

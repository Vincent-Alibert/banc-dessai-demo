"""
Tache « accord » de la chaine : accord entre juges, accord juge IA / humain, et stabilite d'un
juge entre deux passages. Calcule A PART pour chaque version de grille presente dans la campagne
(B-v1.0, B-v1.1, ...) : des notes de deux versions ne se comparent pas (B1 reformule, B0 ajoute).

Lit banc.v_notes_pour_accord (les deux juges factices de cette distribution), calcule avec
banc/notation/accord.py, ecrit banc.fait_accord avec la version de grille dans la cle. Une mesure non
calculable est ecrite avec valeur NULL et son motif, plutot que passee sous silence.

  - accord entre juges IA, passage 1 : alpha de Krippendorff ordinal, par axe et tous axes
    confondus ; kappa quadratique en plus quand il y a exactement deux juges IA ;
  - accord de chaque juge IA avec chaque annotateur humain (verite terrain), passage 1 ;
  - stabilite de chaque juge IA : alpha ordinal entre son passage 1 et son passage 2 ;
  - accord entre annotateurs humains : alpha ordinal par axe et tous axes, sur les dialogues notes par
    les deux (mesure accord_humain_humain_alpha).
    Les familles avec un humain se lisent comme INDICATIVES : sur 24 dialogues, la precision d'un alpha
    est de l'ordre de +-0,15 a +-0,2 (MENTION_INDICATIVE, reprise par la vue v_comparaison_accord).

--inclure-factices (mode demonstration seulement) : lit aussi les juges factices (factice, factice-b),
pour montrer le calcul entre deux juges sans cle. Demonstration du calcul entre deux juges
factices, pas une mesure. Jamais pour une campagne mesuree.

Usage : python entrepot/calculer_accord.py --campagne S01 [--version B-v1.1] [--inclure-factices]
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from banc.campagnes import campagnes  # noqa: E402
from banc.notation.accord import alpha_krippendorff, kappa_pondere  # noqa: E402
from entrepot.connexion import connecter  # noqa: E402
from entrepot.migrer import verifier  # noqa: E402

MENTION_INDICATIVE = ("indicatif : effectif faible, precision de l'ordre de +-0,15 a +-0,2 sur 24 dialogues "
                      "(notation humaine C07)")


class Ecrivain:
    def __init__(self, cnx, campagne: str):
        self.cnx, self.campagne, self.version = cnx, campagne, None

    def __call__(self, axe, mesure, juges, valeur, unites, motif=None):
        self.cnx.execute("""
            INSERT INTO banc.fait_accord (campagne_id, version_grille, axe_id, mesure, juges, valeur, unites, motif)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (campagne_id, version_grille, axe_id, mesure, juges) DO UPDATE SET
              valeur = EXCLUDED.valeur, unites = EXCLUDED.unites, motif = EXCLUDED.motif, calcule_le = now()""",
                         (self.campagne, self.version, axe, mesure, juges,
                          None if valeur is None else round(valeur, 4), unites, motif))
        affiche = "—" if valeur is None else f"{valeur:.3f}"
        print(f"  {self.version:<7} {axe:<5} {mesure:<26} {juges:<26} {affiche:>7}  ({unites} unites)"
              + (f"  {motif}" if motif else ""))


def calculer_version(ecrire: Ecrivain, lignes: list) -> None:
    tous = sorted({j for _, _, j, _, _ in lignes})
    humains = [j for j in tous if j.startswith("humain")]
    juges = [j for j in tous if j not in humains]          # juges IA : accord entre eux
    axes = sorted({a for _, a, _, _, _ in lignes})
    portee = axes + ["tous"] if axes else ["tous"]

    p1 = defaultdict(dict)                                  # (run, axe) -> {juge: valeur}, passage 1
    for run, axe, juge, passage, valeur in lignes:
        if passage == 1:
            p1[(run, axe)][juge] = valeur

    # Accord entre juges IA
    cle = "+".join(juges) if juges else "aucun"
    for axe in portee:
        unites = {u: {j: x for j, x in v.items() if j in juges}
                  for u, v in p1.items() if axe == "tous" or u[1] == axe}
        comparables = sum(1 for v in unites.values() if sum(x is not None for x in v.values()) >= 2)
        if len(juges) < 2:
            ecrire(axe, "alpha_ordinal", cle, None, comparables, "moins de deux juges IA")
            continue
        a = alpha_krippendorff(unites, "ordinal")
        ecrire(axe, "alpha_ordinal", cle, a, comparables,
               None if a is not None else "non calculable (notes toutes identiques ou aucune paire)")
        if len(juges) == 2:
            paires = [(v[juges[0]], v[juges[1]]) for v in unites.values()
                      if v.get(juges[0]) is not None and v.get(juges[1]) is not None]
            k = kappa_pondere([x for x, _ in paires], [y for _, y in paires], [1, 2, 3, 4]) if paires else None
            ecrire(axe, "kappa_quadratique", cle, k, len(paires), None if k is not None else "non calculable")

    # Accord de chaque juge IA avec chaque annotateur humain
    for juge in juges:
        if not humains:
            ecrire("tous", "accord_avec_humain_alpha", juge, None, 0, "pas de notes humaines dans cette version")
            continue
        for humain in humains:
            for axe in portee:
                unites = {u: {juge: v.get(juge), humain: v.get(humain)}
                          for u, v in p1.items() if axe == "tous" or u[1] == axe}
                n = sum(1 for v in unites.values() if None not in v.values())
                a = alpha_krippendorff(unites, "ordinal") if n else None
                ecrire(axe, "accord_avec_humain_alpha", f"{juge}+{humain}", a, n,
                       None if a is not None else ("aucun dialogue note par les deux" if not n else "non calculable"))
    if humains and not juges:
        for humain in humains:
            ecrire("tous", "accord_avec_humain_alpha", humain, None, 0, "aucun juge IA dans cette version")

    # Accord entre annotateurs humains, sur les dialogues notes par les deux ; INDICATIF (MENTION_INDICATIVE)
    for i, h1 in enumerate(humains):
        for h2 in humains[i + 1:]:
            for axe in portee:
                unites = {u: {h1: v.get(h1), h2: v.get(h2)} for u, v in p1.items() if axe == "tous" or u[1] == axe}
                n = sum(1 for v in unites.values() if None not in v.values())
                a = alpha_krippendorff(unites, "ordinal") if n else None
                ecrire(axe, "accord_humain_humain_alpha", f"{h1}+{h2}", a, n,
                       None if a is not None else ("aucun dialogue note par les deux" if not n else "non calculable"))

    # Stabilite de chaque juge IA entre ses passages 1 et 2
    for juge in juges:
        par_passage = defaultdict(dict)
        for run, axe, j, passage, valeur in lignes:
            if j == juge and passage in (1, 2):
                par_passage[(run, axe)][f"p{passage}"] = valeur
        avec_deux = {u: v for u, v in par_passage.items() if "p2" in v}
        if not avec_deux:
            ecrire("tous", "stabilite_alpha", juge, None, 0, "pas de second passage")
            continue
        s = alpha_krippendorff(avec_deux, "ordinal")
        ecrire("tous", "stabilite_alpha", juge, s, len(avec_deux), None if s is not None else "non calculable")


def main() -> int:
    args = sys.argv[1:]
    if "--campagne" not in args:
        sys.exit("[X] --campagne CXX obligatoire")
    from banc.garde import campagne_valide, exiger_simulation
    exiger_simulation()
    campagne = campagne_valide(args[args.index("--campagne") + 1])
    verifier()
    source = "banc.v_notes_pour_accord"
    if "--inclure-factices" in args:
        if not campagnes.est_test(campagne):
            sys.exit("[X] --inclure-factices : reserve aux campagnes de test et de demonstration (S*, C90 a C99)")
        # meme selection que la vue, juges factices compris (humains a part, comme dans la vue)
        source = """(SELECT r.campagne_id, n.run_id, n.axe_id, n.version_grille, j.nom AS juge, n.passage, n.valeur
                       FROM banc.fait_note n JOIN banc.dim_run r USING (run_id) JOIN banc.dim_juge j USING (juge_cle)
                      WHERE r.statut IN ('complet', 'interrompu_produit')) AS notes"""
    with connecter() as cnx:
        versions = [v for (v,) in cnx.execute(
            f"SELECT DISTINCT version_grille FROM {source} WHERE campagne_id = %s ORDER BY 1",
            (campagne,)).fetchall()]
        if "--version" in args:
            versions = [args[args.index("--version") + 1]]
        if not versions:
            # aucune note comparable (juge factice seul, mode demonstration) : la grille courante,
            # pour que le motif « moins de deux juges IA » porte la bonne version
            from entrepot.charger import lire_grille
            versions = [lire_grille(RACINE / "docs" / "evaluation" / "PROMPT-JUGE.md")[0]]
        if "--version" in args:
            cnx.execute("DELETE FROM banc.fait_accord WHERE campagne_id = %s AND version_grille = ANY(%s)",
                        (campagne, versions))
        else:
            # toutes les versions sont recalculees : les lignes d'une version qui n'a plus de notes
            # (ex. une demonstration rejouee) ne doivent pas survivre
            cnx.execute("DELETE FROM banc.fait_accord WHERE campagne_id = %s", (campagne,))
        ecrire = Ecrivain(cnx, campagne)
        print(f"accord {campagne} : versions de grille {versions}")
        for version in versions:
            ecrire.version = version
            lignes = cnx.execute(f"SELECT run_id, axe_id, juge, passage, valeur FROM {source} "
                                 "WHERE campagne_id = %s AND version_grille = %s", (campagne, version)).fetchall()
            calculer_version(ecrire, lignes)
            if any(j.startswith("humain") for _, _, j, _, _ in lignes):
                print(f"  {MENTION_INDICATIVE} (accord avec un humain et entre humains)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

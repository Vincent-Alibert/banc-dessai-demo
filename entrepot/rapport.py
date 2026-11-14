"""
Tache « rapport » de la chaine : rapport v0 d'une campagne, une page HTML autonome, lue
uniquement dans les vues de l'entrepot (banc.v_*). Aucun chiffre n'est calcule ici : ce que
la page affiche est ce que la base contient.

Sortie : runs/<campagne>/rapport_v0.html (runs/ n'est pas suivi par git).

Usage : python entrepot/rapport.py --campagne C01
"""
from __future__ import annotations

import html
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
from entrepot.connexion import connecter  # noqa: E402
from entrepot.migrer import verifier  # noqa: E402

SECTIONS = [
    ("Runs", "Un run = le vrai lanceur avec des réponses enregistrées d'un persona fictif et de Noa.",
     "SELECT persona, bras, statut, code_notation AS code, tours_joues || '/' || tours_demandes AS tours, "
     "duree_totale_s AS duree_s, round(cout_noa_usd, 6) AS cout_noa_usd, incidents_graves "
     "FROM banc.v_runs WHERE campagne_id = %s ORDER BY persona, bras"),
    ("Dépenses de la simulation", "Tous les appels externes et les jetons sont à zéro. Les notes factices ne sont pas une évaluation.",
     "SELECT poste, source, runs, appels, jetons, cout_usd FROM banc.v_couts_par_campagne "
     "WHERE campagne_id = %s ORDER BY poste"),
    ("Scénario budgétaire simulé", "Coût historique complet C01 utilisé pour le budget et les réservations ; jamais une dépense nouvelle, non proratisé si moins de tours.",
     "SELECT dialogues, cout_simule_usd, provenance FROM banc.v_couts_simules WHERE campagne_id = %s"),
    ("Incidents du scénario", "Les origines produit, banc et fournisseur décrivent le jeu enregistré. "
                              "Aucune observation nouvelle d'un produit ou fournisseur externe.",
     "SELECT type, gravite, origine, nombre, runs FROM banc.v_incidents WHERE campagne_id = %s "
     "ORDER BY CASE gravite WHEN 'bloquant' THEN 0 WHEN 'majeur' THEN 1 WHEN 'mineur' THEN 2 ELSE 3 END, nombre DESC"),
    # {grilles} : grille(s) reellement utilisee(s) par la campagne, lue(s) dans l'entrepot
    ("Notes par axe", "{grilles}, notes de 1 à 4 ; « sans objet » quand l'axe ne s'observe pas (B6 ; B4 et B5 "
                      "en grille v1.3 si le dialogue s'arrête tôt). Le juge factice donne des notes fixes : il "
                      "prouve la chaîne, il ne juge rien.",
     "SELECT version_grille AS grille, juge, type_juge, passage, axe_id AS axe, libelle, notes, sans_objet, "
     "moyenne, ecart_type, minimum, maximum FROM banc.v_notes_par_axe WHERE campagne_id = %s "
     "ORDER BY version_grille, juge, passage, axe_id"),
    ("Accord entre juges factices", "Démonstration des algorithmes d'alpha ordinal et de kappa quadratique sur des notes déterministes : pas une mesure de qualité, aucun humain.",
     "SELECT version_grille AS grille, axe_id AS axe, mesure, juges, valeur, unites, motif FROM banc.v_accord "
     "WHERE campagne_id = %s AND mesure IN ('alpha_ordinal', 'kappa_quadratique') ORDER BY mesure, axe_id"),

]

STYLE = """
:root { --fond:#fbfaf7; --texte:#1f2328; --discret:#5b616b; --ligne:#e3e0d8; --entete:#f1eee6; }
@media (prefers-color-scheme: dark) { :root { --fond:#16181c; --texte:#e6e6e3; --discret:#a0a4ab;
  --ligne:#2c3037; --entete:#20242a; } }
body { background:var(--fond); color:var(--texte); font:15px/1.5 system-ui, sans-serif; margin:0; }
main { max-width:1100px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:17px; margin:32px 0 4px; }
p { color:var(--discret); margin:0 0 10px; } .meta { font-size:13px; }
.defile { overflow-x:auto; } table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { border-bottom:1px solid var(--ligne); padding:6px 8px; text-align:left; white-space:nowrap; }
th { background:var(--entete); font-weight:600; } td.nombre { text-align:right; font-variant-numeric:tabular-nums; }
.vide { color:var(--discret); font-style:italic; }
"""


def tableau(colonnes, lignes) -> str:
    if not lignes:
        return '<p class="vide">Aucune ligne.</p>'
    tete = "".join(f"<th>{html.escape(c)}</th>" for c in colonnes)
    corps = []
    for l in lignes:
        cellules = []
        for v in l:
            nombre = isinstance(v, (int, float)) or type(v).__name__ == "Decimal"
            texte = "—" if v is None else str(v)
            cellules.append(f'<td{" class=nombre" if nombre else ""}>{html.escape(texte)}</td>')
        corps.append("<tr>" + "".join(cellules) + "</tr>")
    return f'<div class="defile"><table><thead><tr>{tete}</tr></thead><tbody>{"".join(corps)}</tbody></table></div>'


def main() -> int:
    args = sys.argv[1:]
    if "--campagne" not in args:
        sys.exit("[X] --campagne CXX obligatoire")
    from banc.garde import campagne_valide, exiger_simulation
    exiger_simulation()
    campagne = campagne_valide(args[args.index("--campagne") + 1])
    verifier()
    parties = []
    with connecter() as cnx:
        objet = cnx.execute("SELECT objet FROM banc.dim_campagne WHERE campagne_id = %s", (campagne,)).fetchone()
        if not objet:
            sys.exit(f"[X] campagne {campagne} absente de l'entrepot (lancer entrepot/charger.py)")
        grilles = [r[0] for r in cnx.execute(
            "SELECT DISTINCT version_grille FROM banc.v_notes_par_axe WHERE campagne_id = %s ORDER BY 1",
            (campagne,)).fetchall()]
        libelle_grilles = ("Grille " + ", ".join(g.replace("B-v", "B v") for g in grilles)) if grilles             else "Aucune note dans l'entrepôt pour cette campagne"
        for titre, aide, sql in SECTIONS:
            aide = aide.replace("{grilles}", libelle_grilles)
            cur = cnx.execute(sql, (campagne,))
            parties.append(f"<h2>{html.escape(titre)}</h2><p>{html.escape(aide)}</p>"
                           + tableau([d.name for d in cur.description], cur.fetchall()))
    maintenant = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    page = (f'<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>Rapport {html.escape(campagne)}</title><style>{STYLE}</style></head><body><main>"
            f"<h1>Banc d'essai Noa — simulation {html.escape(campagne)}</h1>"
            f'<p class="meta">{html.escape(objet[0] or "")}</p>'
            f'<p class="meta">Rapport v0, produit le {maintenant} à partir des vues de l\'entrepôt. '
            f"SIMULATION : données synthétiques enregistrées, aucune API appelée, dépenses nulles.</p>"
            + "".join(parties) + "</main></body></html>")
    sortie = RACINE / "runs" / campagne / "rapport_v0.html"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(page, encoding="utf-8")
    print(f"[ok] {sortie.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

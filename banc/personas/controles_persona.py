"""
Controles Python du persona, lances APRES les dialogues (tache « controles » du DAG, entre
« dialogues » et « juges ») : ils lisent les runs ecrits, n'appellent rien, ne regenerent rien
et ne calculent aucun score global de fidelite. Ce sont des indicateurs, a lire run par run.

Pour chaque run de dialogue de la campagne (les tests a entree fixe n'ont pas de persona : ignores) :

  1. Longueur, sur l'ensemble du dialogue : messages ECRITS par le modele persona (tours 2 et
     suivants ; le tour 1 est le premier message de la fiche ou le bouton d'accueil) : nombre,
     moyenne et maximum de mots, part de reponses courtes (MOTS_COURTS mots ou moins), messages
     coupes au plafond ; compares a la fiche : longueur moyenne attendue (style.longueur_moyenne_mots)
     et plafond de coupe du style. Ecart signale, pas corrige.
  2. Statut de fin : statut_fin, motif_fin, tour_fin du run, et la forme des decisions du persona v2
     (stricte, toleree, repli) ; un run sans statut_fin (C00 a C05) recoit la valeur derivee de
     l'entrepot (MAX_TURNS si tous les tours sont joues, TECHNICAL_ERROR sinon), marquee « derive ».
  3. Secret revele des le tour 1 : un element de « ne_dira_pas_sans_confiance » se lit-il dans le
     premier message de l'utilisateur ? Repere lexical grossier (mots pleins du secret retrouves dans
     le message, voir secret_present) ; on note aussi le premier tour ou il affleure, quel qu'il soit.
     Un faux positif se relit dans le transcript.
  4. Coherence du depart (persona v2.2, regle de coherence_depart.py) : un depart
     QUIT_NEGATIVE dont le message non envoye continue la conversation (question, demande, confidence)
     est marque incoherent ; agace, sarcastique ou cloture partielle avec un motif : ambigu
     (fin.depart_categorie = coherent, incoherent ou ambigu, avec la raison ; fin.depart_incoherent
     garde le booleen) ; null sans depart QUIT_NEGATIVE. Seul un depart coherent est un abandon.
     S'applique aussi aux runs v2.1 deja ecrits : le depart y a eu lieu, il est seulement
     marque. En v2.2, un depart incoherent est deja ecarte pendant le dialogue (traite comme CONTINUE) :
     fin.departs_incoherents_ecartes les compte (null avant v2.2).

Sortie : runs/<C>/controles/persona.json (une entree par run), chargee dans l'entrepot
(banc_brut.controle_persona -> banc.fait_controle_persona).

Usage :  python -m banc.personas.controles_persona --campagne CXX
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from banc import chemins
from banc.garde import campagne_valide, exiger_simulation, personas_valides
from banc.personas.coherence_depart import controler_depart, departs_ecartes

RUNS = chemins.RUNS
VERSION_CONTROLES = "1.2"
MOTS_COURTS = 5          # une reponse de 5 mots ou moins est « courte » (« ok », « jsp », « bof un peu »)
SEUIL_LONGUEUR = 1.5     # moyenne > 1,5 x la longueur attendue de la fiche : signalee « trop long »
PERSONAS = chemins.PERSONAS

# Plafonds de coupe par style (fiche_banc.PLAFONDS_MOTS), recopies pour que ce module ne charge pas
# pydantic ni les fiches : il ne lit que les runs.
PLAFONDS_MOTS = {"telegraphique": 25, "courant": 40, "emotionnel": 80, "verbeux": 120}

# Mots vides ignores dans le repere du secret (formes sans accents, en minuscules).
_MOTS_VIDES = set("""
qu que qui quoi quand comme dans pour avec sans sous sur chez entre depuis pendant avant apres encore
plus moins tres trop tout tous toute toutes rien personne jamais toujours deja aussi meme cette ces
son sa ses leur leurs mon ma mes ton ta tes notre votre elle elles il ils lui eux nous vous on je tu
est sont etait ete avoir etre fait faire dit dire peut veut doit va vont parce alors donc mais
""".split())


def _plat(texte: str) -> str:
    t = unicodedata.normalize("NFKD", texte.replace("’", "'")).encode("ascii", "ignore").decode("ascii")
    return t.lower()


def _racines(texte: str) -> list[str]:
    """Mots pleins (5 lettres et plus, hors mots vides), reduits a leurs 5 premieres lettres :
    « enterrement » et « enterree » se rejoignent, « dort » et « dormir » non (repere grossier)."""
    mots = re.findall(r"[a-z]+", _plat(texte))
    return [m[:5] for m in mots if len(m) >= 5 and m not in _MOTS_VIDES]


def secret_present(secret: str, message: str) -> bool:
    """Au moins la moitie des mots pleins du secret, et au moins deux, se retrouvent dans le message
    (un seul s'il n'en a qu'un)."""
    racines = sorted(set(_racines(secret)))
    if not racines:
        return False
    vus = set(_racines(message))
    communs = sum(1 for r in racines if r in vus)
    return communs >= max(1 if len(racines) == 1 else 2, (len(racines) + 1) // 2)


def statut_derive(run: dict) -> tuple[str, int, bool]:
    """(statut_fin, tour_fin, derive). Les runs d'avant le schema 0.5 (C00 a C05, rejeux de C01) n'ont pas de statut
    de fin : il est derive, meme regle que l'entrepot (dbt/models/etoile/dim_run.sql)."""
    tours = run.get("tours") or []
    tour_fin = run.get("tour_fin")
    if tour_fin is None:
        tour_fin = sum(1 for t in tours if t.get("noa"))
    if run.get("statut_fin"):
        return run["statut_fin"], tour_fin, False
    demandes = (run.get("resume") or {}).get("tours_demandes") or len(tours)
    return ("MAX_TURNS" if tour_fin >= demandes else "TECHNICAL_ERROR"), tour_fin, True


def longueur_attendue(run: dict) -> tuple[int | None, str]:
    """Longueur moyenne attendue : copiee dans le run s'il la porte ; sinon lue dans la fiche
    courante de personas/ (source « fiche_courante » : la fiche a pu changer depuis le run)."""
    p = run.get("persona") or {}
    if p.get("longueur_moyenne_mots") is not None:
        return int(p["longueur_moyenne_mots"]), "run"
    f = PERSONAS / f"{p.get('identifiant')}.json"
    if f.exists():
        try:
            return int(json.loads(f.read_text(encoding="utf-8"))["fiche"]["style"]["longueur_moyenne_mots"]), \
                "fiche_courante"
        except (KeyError, ValueError, TypeError):
            pass
    return None, "inconnue"


def controler_run(run: dict, fichier: str) -> dict:
    persona = run.get("persona") or {}
    tours = [t for t in (run.get("tours") or []) if t.get("persona")]
    ecrits = [t for t in tours if (t.get("n") or 0) >= 2]          # messages produits par le modele persona
    mots = [len(t["persona"].split()) for t in ecrits]
    style = str((persona.get("axes") or {}).get("style_ecriture") or "")
    plafond = (run.get("modele_persona") or {}).get("plafond_mots") or PLAFONDS_MOTS.get(style)
    attendue, source_attendue = longueur_attendue(run)
    moyenne = round(sum(mots) / len(mots), 1) if mots else None
    ratio = round(moyenne / attendue, 2) if moyenne is not None and attendue else None

    statut, tour_fin, derive = statut_derive(run)
    formes = [t.get("persona_decision_forme") for t in ecrits if t.get("persona_decision_forme")]
    relances = sum(1 for t in ecrits if t.get("persona_decision_relance"))
    depart = run.get("depart") or {}
    if depart.get("decision_forme"):
        formes.append(depart["decision_forme"])
        relances += 1 if depart.get("decision_relance") else 0

    coherence = controler_depart(run)
    # v2.2 : la categorie decidee pendant le dialogue est celle qui a compte ; sinon, marquage a posteriori
    categorie = None if coherence is None else (coherence["categorie_au_dialogue"] or coherence["categorie"])

    secrets = [s for s in (persona.get("ne_dira_pas_sans_confiance") or []) if s]
    premier =next((t for t in tours if t.get("n") == 1), None)
    tour_1 = [s for s in secrets if premier and premier.get("ouverture") != "bouton"
              and secret_present(s, premier["persona"])]
    premier_tour_secret = None
    for t in tours:
        if any(secret_present(s, t["persona"]) for s in secrets):
            premier_tour_secret = t.get("n")
            break

    return {
        "fichier_run": fichier, "persona": persona.get("identifiant"), "bras": run.get("bras"),
        "version_persona": run.get("version_persona") or "v1",
        "longueur": {
            "messages_ecrits": len(mots), "moyenne_mots": moyenne, "maximum_mots": max(mots) if mots else None,
            "part_courtes": round(sum(1 for m in mots if m <= MOTS_COURTS) / len(mots), 2) if mots else None,
            "seuil_court_mots": MOTS_COURTS,
            "coupes_au_plafond": sum(1 for t in ecrits if t.get("persona_coupe_au_plafond")),
            "attendue_mots": attendue, "source_attendue": source_attendue, "plafond_mots": plafond,
            "ratio_moyenne_attendue": ratio,
            "trop_long": bool(ratio is not None and ratio > SEUIL_LONGUEUR),
            "au_plafond": bool(plafond and mots and max(mots) >= plafond),
        },
        "fin": {
            "statut_fin": statut, "statut_derive": derive, "motif_fin": run.get("motif_fin"), "tour_fin": tour_fin,
            "tours_demandes": (run.get("resume") or {}).get("tours_demandes"),
            "decisions_stricte": formes.count("stricte"), "decisions_toleree": formes.count("toleree"),
            "decisions_repli": formes.count("repli"),
            # relance de la seule decision : decisions obtenues par la relance et relances faites ;
            # decisions_repli = replis RESTANTS apres relance
            "decisions_relance": formes.count("relance"), "relances": relances,
            # categorie du depart QUIT_NEGATIVE au regard de son message non envoye (null sans un tel depart) :
            # coherent (abandon), incoherent ou ambigu (ni l'un ni l'autre : pas un abandon, a relire)
            "depart_categorie": categorie,
            "depart_categorie_raison": None if coherence is None else coherence["raison"],
            "depart_incoherent": None if coherence is None else categorie == "incoherent",
            "depart_incoherent_raison": None if coherence is None else coherence["raison"],
            # persona v2.2 : departs incoherents deja ecartes pendant le dialogue (null avant v2.2)
            "departs_incoherents_ecartes": departs_ecartes(run) if run.get("version_persona") == "v2.2" else None,
        },
        "secret": {
            "elements": len(secrets),
            "revele_tour_1": bool(tour_1), "elements_tour_1": tour_1,
            "premier_tour_affleure": premier_tour_secret,
            "methode": "mots pleins du secret retrouves dans le message (repere lexical, a relire)",
        },
    }


def controler(campagne: str, runs: Path | None = None) -> dict:
    dossier = (runs or RUNS) / campagne
    resultats, ignores = [], 0
    for f in sorted(dossier.glob(f"{campagne}-*.json")):
        try:
            run = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(run, dict) or "persona" not in run or run.get("type_run", "dialogue") != "dialogue":
            ignores += 1          # spike de C00, tests a entree fixe : pas de persona a controler
            continue
        resultats.append(controler_run(run, f.name))
    sortie = {"campagne": campagne, "version_controles": VERSION_CONTROLES,
              "horodatage_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "seuils": {"mots_courts": MOTS_COURTS, "trop_long_si_ratio_au_dessus": SEUIL_LONGUEUR},
              "runs_ignores": ignores, "runs": resultats}
    if resultats:
        (dossier / "controles").mkdir(parents=True, exist_ok=True)
        (dossier / "controles" / "persona.json").write_text(json.dumps(sortie, ensure_ascii=False, indent=2),
                                                            encoding="utf-8")
    return sortie


def main() -> int:
    args = sys.argv[1:]
    if "--campagne" not in args:
        sys.exit("Usage : python -m banc.personas.controles_persona --campagne CXX")
    exiger_simulation()
    campagne = campagne_valide(args[args.index("--campagne") + 1])
    r = controler(campagne)
    print(f"controles du persona — {campagne} : {len(r['runs'])} run(s), {r['runs_ignores']} ignore(s)")
    print(f"  {'persona':<11} {'bras':<16} {'v':<4} {'msg':>3} {'moy':>5} {'max':>4} {'court':>5} {'att.':>4} "
          f"{'ratio':>5}  {'fin':<15} {'T':>2} {'repli':>5}  {'depart':<10} secret")
    for c in r["runs"]:
        lg, fin, sec = c["longueur"], c["fin"], c["secret"]
        print(f"  {c['persona'] or '?':<11} {c['bras'] or '-':<16} {c['version_persona']:<4} {lg['messages_ecrits']:>3} "
              f"{lg['moyenne_mots'] if lg['moyenne_mots'] is not None else '-':>5} "
              f"{lg['maximum_mots'] if lg['maximum_mots'] is not None else '-':>4} "
              f"{lg['part_courtes'] if lg['part_courtes'] is not None else '-':>5} "
              f"{lg['attendue_mots'] if lg['attendue_mots'] is not None else '-':>4} "
              f"{lg['ratio_moyenne_attendue'] if lg['ratio_moyenne_attendue'] is not None else '-':>5}"
              f"{'!' if lg['trop_long'] else ' '} {fin['statut_fin'] + ('*' if fin['statut_derive'] else ''):<15} "
              f"{fin['tour_fin']:>2} {fin['decisions_repli']:>5}  "
              f"{(fin['depart_categorie'] or '-').upper() if fin['depart_categorie'] in ('incoherent', 'ambigu') else fin['depart_categorie'] or '-':<10} "
              f"{'TOUR 1' if sec['revele_tour_1'] else ('tour ' + str(sec['premier_tour_affleure']) if sec['premier_tour_affleure'] else '-')}")
    print("  ! moyenne au-dessus de 1,5 x la longueur attendue · * statut derive (run d'avant le schema 0.5 : C00 a C05, rejeux de C01)")
    print("  depart : categorie d'un QUIT_NEGATIVE au regard de son message non envoye (coherence_depart.py) :"
          " coherent = abandon ; INCOHERENT et AMBIGU ne comptent pas comme abandon")
    ambigus = [c for c in r["runs"] if c["fin"]["depart_categorie"] == "ambigu"]
    if ambigus:
        print(f"  departs AMBIGUS a relire ({len(ambigus)}) : " + ", ".join(c["fichier_run"] for c in ambigus))
    # Indicateurs seulement : jamais d'echec de la chaine sur un controle du persona.
    return 0


if __name__ == "__main__":
    sys.exit(main())

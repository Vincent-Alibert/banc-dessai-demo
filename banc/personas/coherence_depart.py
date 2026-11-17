"""
Coherence d'un depart du persona (persona v2.2).

Un QUIT_NEGATIVE dont le message, non envoye, poursuit la conversation (le persona livre son secret, demande
« Que faire concretement ? », pose une question) contredit son propre message : c'est un artefact du persona,
pas un abandon. Mais un message agace ou sarcastique (« genre quoi ? », « j'ai pas trop envie »), ou qui
ferme a moitie en posant encore une question, peut etre un vrai depart : ni l'un ni l'autre, AMBIGU.

Trois categories pour un QUIT_NEGATIVE (seul controle : c'est l'abandon qu'on mesure) :
  - coherent   : un vrai depart ; compte comme abandon ;
  - incoherent : le message continue la conversation ; en v2.2, traite comme CONTINUE (le message part
                 chez Noa) ; jamais un abandon ;
  - ambigu     : signaux contraires, avec un motif de depart ; en v2.2, le dialogue s'arrete comme pour un
                 depart (le message n'est pas envoye) mais le depart ne compte pas comme abandon et va dans
                 la liste a relire.

Regle (volontairement simple, a relire dans le transcript), dans cet ordre :
  1. message vide : coherent (le format demande de partir sans rien ecrire) ;
  2. on repere : une formule de depart (fermer l'appli, en rester la, m'arreter, a plus, je te laisse...),
     une question (« ? »), une demande (que faire, aide-moi, dis-moi, tu peux...), une confidence a la
     premiere personne (je me sens, j'ai peur, j'ai l'impression, je fais semblant, ca m'a epuise...),
     un marqueur d'agacement, de sarcasme ou de refus (genre quoi, j'ai pas envie, j'aime pas, bof,
     chelou, n'importe quoi, ca sert a rien, laisse tomber...) ;
  3. formule de depart : coherent, sauf si le message pose aussi une question ou une demande (cloture
     partielle) : ambigu ;
  4. sans formule de depart, ni question, ni demande, ni confidence : coherent (retrait sec ou agace :
     « t'as rien compris », « bof », « j'aime pas ca ») ;
  5. sans formule de depart, le message continue la conversation (question, demande ou confidence) :
     ambigu s'il est agace, sarcastique ou refuse quelque chose ; incoherent sinon ;
  6. ambigu exige un motif de depart (ligne DECISION) : sans motif, un cas ambigu est incoherent.

En v2.2, moteur_persona applique la regle a chaque decision. Sur des runs deja ecrits (v2.1), elle relit
depart.message_non_envoye et depart.motif et marque le depart, sans rien rejouer : controles_persona.py
(fin.depart_categorie) ou ce script, en lecture seule, qui liste les ambigus a part pour la relecture.

Ce module n'importe rien d'autre que la bibliotheque standard : aucun appel reseau, aucune cle.

Usage :  python -m banc.personas.coherence_depart --source runs/C12 [--source runs/C13 ...]     (lecture seule)
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

VERSION_REGLE = "1.1"
CATEGORIES = ("coherent", "incoherent", "ambigu")


def _plat(texte: str) -> str:
    """Minuscules, sans accents, apostrophes droites : « J’ai » et « jai » se lisent pareil plus bas."""
    t = unicodedata.normalize("NFKD", texte.replace("’", "'").replace("`", "'"))
    return t.encode("ascii", "ignore").decode("ascii").lower()


# Formules de depart explicites (texte aplati). « arreter la » seulement en fin de proposition, pour ne
# pas prendre « arreter la cigarette » pour un depart.
_DEPART = re.compile(
    r"ferm\w*\s+(l'?\s*)?app"  # fermer l'appli, je ferme l'application
    r"|en\s+rest\w*\s+la\b"  # on en reste la, qu'on en reste la
    r"|\bm'?\s*arret\w*\s*(la|ici|pour|[.!,;…]|$)"  # je vais m'arreter (ici, la, pour ce soir)
    r"|\b(j'?\s*arrete|on\s+arrete|je\s+(prefere\s+)?arreter)\b"
    r"|\barret\w*\s+(la|ici)\s*([.!,;…]|$)"
    r"|\ba\s*(\+|plus|demain|bientot|la\s+prochaine)\s*([.!,;…]|$|noa\b)"  # pas « il y a plus de »
    r"|\bau\s+revoir\b|\bbonne\s+(soiree|journee|nuit)\b|\bciao\b|\bbye\b"
    r"|\bje\s+(te|vous)\s+laisse\b|\bje\s+vais\s+y\s+aller\b|\bje\s+m'?\s*en\s+vais\b"
    r"|\bje\s+(me\s+)?(deconnecte|retire)\b|\bje\s+pars\b|\bje\s+vais\s+partir\b"
    r"|\bfaire\s+autre\s+chose\b|\blaisse\s+tomber\b|\bc'?\s*est\s+bon\s+pour\s+(moi|aujourd'?hui|ce\s+soir)\b"
)

# Demande d'aide ou de conseil.
_DEMANDE = re.compile(
    r"\bque\s+faire\b|\bqu'?\s*est[- ]ce\s+que\s+je\s+(dois|peux|fais)\b|\bcomment\s+(je|faire|on)\b"
    r"|\baide[- ]?moi\b|\bdis[- ]?moi\b|\btu\s+(peux|pourrais|saurais)\b|\bvous\s+(pouvez|pourriez)\b"
    r"|\bj'?\s*ai\s+besoin\b|\bje\s+voudrais\s+(savoir|que\s+tu)\b|\bt'?\s*as\s+une\s+idee\b"
)

# Confidence a la premiere personne : ce que la personne ressent, vit ou a vecu. « jai » sans
# apostrophe (style telegraphique de Sebastien) compris.
_CONFIDENCE = re.compile(
    r"\bje\s+me\s+sens\b|\bj'?\s*ai\s+(peur|honte|l'?\s*impression|du\s+mal|mal\b)"
    r"|\bje\s+fais\s+semblant\b|\bj'?\s*ai\s+(perdu|essaye|culpabilise|arrete\s+de|plus\s+envie|pas\s+dormi)"
    r"|\bje\s+(culpabilise|stresse|panique|angoisse|pleure|dors\s+(plus|pas|mal))\b"
    r"|\bca\s+m'?\s*(a|epuise|angoisse|fait|pese|ronge|tue)\b|\bdepuis\s+que\s+j"
    r"|\bje\s+suis\s+(perdu|epuise|fatigue|crevee?|seule?|nul|triste|mal|a\s+bout|deborde)"
    r"|\bje\s+(n'?\s*)?(arrive|y\s+arrive)\s+(pas|plus)\b|\bc'?\s*est\s+dur\b"
)

# Agacement, sarcasme ou refus (et variantes proches).
_AGACEMENT = re.compile(
    r"\bgenre\s+quoi\b|\bj'?\s*(n'?\s*)?ai\s+(pas|plus)\s+(trop\s+|vraiment\s+|tellement\s+)?envie\b"
    r"|\bpas\s+(trop\s+)?envie\s+de\b|\bj'?\s*(n'?\s*)?aime\s+pas\b|\bje\s+n'?\s*aime\s+pas\b"
    r"|\blaisse\s+tomber\b|\bbof\b|\bchelou\b|\bn'?\s*importe\s+quoi\b|\bca\s+sert\s+a\s+rien\b"
    r"|\bj'?\s*en\s+ai\s+marre\b|\bpff+\b|\bosef\b|\bca\s+me\s+(saoule|soule|gonfle)\b|\btu\s+me\s+(saoules|soules)\b"
    # reproche fait a Noa (un refus de ce qu'il vient de dire)
    r"|\bj'?\s*ai\s+(rien|jamais)\s+dit\b|\bt'?\s*as\s+rien\s+compris\b|\btu\s+(ne\s+)?comprends\s+rien\b"
    r"|\bpourquoi\s+tu\s+me\s+(parles|sors|dis)\b|\bc'?\s*est\s+pas\s+(ce\s+que|ca\s+que)\s+j"
)


@dataclass
class Coherence:
    categorie: str  # coherent, incoherent ou ambigu
    raison: str  # pourquoi, en quelques mots (lisible dans le run et dans les controles)

    @property
    def coherent(self) -> bool:
        return self.categorie == "coherent"


def _trouve(motif: re.Pattern, texte: str) -> str | None:
    m = motif.search(texte)
    return m.group(0).strip() if m else None


def coherence_depart(decision: str | None, message: str | None, motif: str | None = None) -> Coherence:
    """Categorie d'un depart au regard du message ecrit en meme temps (regle du module, dans l'ordre).
    motif : le motif de la ligne DECISION ; un cas ambigu sans motif est incoherent."""
    if decision != "QUIT_NEGATIVE":
        return Coherence("coherent", "pas un QUIT_NEGATIVE")
    texte = (message or "").strip()
    if not texte:  # 1
        return Coherence("coherent", "depart sans message")
    plat = _plat(texte)  # 2
    depart = _trouve(_DEPART, plat)
    question = "?" in plat
    demande = _trouve(_DEMANDE, plat)
    confidence = _trouve(_CONFIDENCE, plat)
    agacement = _trouve(_AGACEMENT, plat)
    a_motif = bool((motif or "").strip())

    def ambigu(raison: str) -> Coherence:  # 6
        if a_motif:
            return Coherence("ambigu", raison)
        return Coherence("incoherent", raison + ", sans motif de depart")

    if depart:  # 3
        if question or demande:
            return ambigu(
                f"cloture partielle : formule de depart (« {depart} ») et "
                + ("question" if question else f"demande (« {demande} »)")
            )
        return Coherence("coherent", f"formule de depart dans le message (« {depart} »)")
    if not (question or demande or confidence):  # 4
        return Coherence(
            "coherent",
            "retrait sans question, demande ni confidence" + (f" (agace : « {agacement} »)" if agacement else ""),
        )
    poursuite = (
        "le message pose une question"
        if question
        else f"le message demande quelque chose (« {demande} »)"
        if demande
        else f"le message confie quelque chose (« {confidence} »)"
    )
    if agacement:  # 5
        return ambigu(f"{poursuite}, mais agace ou refuse (« {agacement} »)")
    return Coherence("incoherent", poursuite)


def controler_depart(run: dict) -> dict | None:
    """Pour un run deja ecrit : categorie du depart QUIT_NEGATIVE, d'apres depart.message_non_envoye et
    depart.motif. None si le run n'a pas de depart QUIT_NEGATIVE (rien a controler)."""
    d = run.get("depart") or {}
    if d.get("statut") != "QUIT_NEGATIVE":
        return None
    c = coherence_depart("QUIT_NEGATIVE", d.get("message_non_envoye"), d.get("motif"))
    return {
        "categorie": c.categorie,
        "coherent": c.coherent,
        "raison": c.raison,
        "apres_tour": d.get("apres_tour"),
        "forme": d.get("decision_forme"),
        "relance": bool(d.get("decision_relance")),
        "decision_brute": d.get("decision_brute"),
        "motif": d.get("motif"),
        "message_non_envoye": d.get("message_non_envoye"),
        "version_regle": VERSION_REGLE,
        # v2.2 : categorie decidee pendant le dialogue (celle qui a compte), sinon None
        "categorie_au_dialogue": d.get("categorie"),
    }


def departs_ecartes(run: dict) -> int:
    """Persona v2.2 : nombre de QUIT_NEGATIVE incoherents traites comme CONTINUE pendant le dialogue."""
    return sum(1 for t in (run.get("tours") or []) if t.get("persona_depart_incoherent"))


def main() -> int:
    args = sys.argv[1:]
    sources = [Path(args[i + 1]) for i, a in enumerate(args) if a == "--source" and i + 1 < len(args)]
    if not sources:
        sys.exit("Usage : python -m banc.personas.coherence_depart --source runs/C12 [--source runs/C13 ...]")
    par_categorie: dict[str, list[tuple[str, dict]]] = {c: [] for c in CATEGORIES}
    for source in sources:
        for f in sorted(source.glob("C*-*.json")):
            try:
                run = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if not isinstance(run, dict) or run.get("type_run", "dialogue") != "dialogue":
                continue
            c = controler_depart(run)
            if c is not None:
                par_categorie[c["categorie_au_dialogue"] or c["categorie"]].append((f.stem, c))
    print(f"coherence des departs QUIT_NEGATIVE (regle {VERSION_REGLE}, lecture seule)")
    for categorie, titre in (
        ("coherent", "COHERENTS (comptent comme abandon)"),
        ("incoherent", "INCOHERENTS (pas un abandon ; v2.2 : traites comme CONTINUE)"),
    ):
        print(f"\n{titre} : {len(par_categorie[categorie])}")
        for nom, c in par_categorie[categorie]:
            print(f"  {nom:<48} T{c['apres_tour']} {c['forme'] or '-':<8} {c['raison']}")
    print(f"\nAMBIGUS, A RELIRE (pas un abandon, dialogue arrete) : {len(par_categorie['ambigu'])}")
    for nom, c in par_categorie["ambigu"]:
        print(
            f"  {nom} — T{c['apres_tour']} {c['forme'] or '-'} — {c['raison']}\n"
            f"      message non envoye : {c['message_non_envoye']}\n      motif : {c['motif']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Notation déterministe : les deux juges factices du banc, sans API ni clé.
Les notes démontrent les calculs, pas la qualité de Noa.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from banc import chemins
from banc.campagnes.parallele import (
    PARALLELES_MAX,
    Plafond,
    attendre_interruptible,
    compter_appel,
    definir_campagne_direct,
    envoyer_direct,
    envoyer_jauge,
    executer,
    verifier_interruption,
    verrou_lanceur,
)

from banc.garde import campagne_valide, exiger_simulation
from banc.dialogues.simulation import reseau_interdit

RUNS = chemins.RUNS
PROMPT_FICHIER = chemins.PROMPT_JUGE
CRITERES = ("B0", "B1", "B2", "B3", "B4", "B5", "B6")  # B0 depuis la grille v1.1
# Grille v1.3 : B4 et B5 « sans objet » permis seulement sur un dialogue de moins de 3 tours (depart
# precoce du persona v2, test a entree fixe). Meme seuil que le test dbt b4_b5_sans_objet_si_depart_precoce.
AXES_DEPART_PRECOCE = ("B4", "B5")
TOURS_MIN_B4_B5 = 3
# Delai d'attente par appel (Gemini 3.1 Pro preview peut mettre 13 min) : un appel qui depasse
# est enregistre en echec et relance une fois au plus (boucle des essais).
DELAI_MAX_S = 120

MODELES = {"factice": "factice-v1", "factice-b": "factice-b-v1"}


def version_prompt() -> str:
    m = re.search(r"version (\d+\.\d+)", PROMPT_FICHIER.read_text(encoding="utf-8"))
    return f"grille-B-v{m.group(1)}" if m else "grille-B-inconnue"


def prompt_juge() -> str:
    texte = PROMPT_FICHIER.read_text(encoding="utf-8")
    m = re.search(r"<<<PROMPT>>>\n(.*?)\n<<<FIN>>>", texte, re.S)
    if not m:
        sys.exit("[X] balises <<<PROMPT>>> / <<<FIN>>> introuvables dans docs/evaluation/PROMPT-JUGE.md")
    return m.group(1).strip()


def extraire_json(texte: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", texte.strip(), flags=re.S)
    i = t.find("{")
    if i < 0:
        raise ValueError("pas d'objet JSON")
    obj, _ = json.JSONDecoder().raw_decode(t[i:])
    return obj


def valider(notes: dict, ident: str, nullables: tuple[str, ...] = ("B6",)) -> dict:
    """Notes entieres de 1 a 4, nulles seulement sur les axes de `nullables` (B6 ; B4 et B5 aussi en
    grille v1.3 sur un dialogue court), justification par critere. Leve sinon."""
    if notes.get("id") != ident:
        raise ValueError(f"id attendu {ident}, recu {notes.get('id')!r}")
    propre = {"id": ident, "justifications": {}}
    just = notes.get("justifications") or {}
    for c in CRITERES:
        v = notes.get(c)
        if c in nullables and v is None:
            propre[c] = None
        elif isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 4:
            raise ValueError(f"{c} = {v!r}, attendu un entier de 1 a 4" + (" ou null" if c in nullables else ""))
        else:
            propre[c] = v
        propre["justifications"][c] = str(just.get(c, "")).strip()
    return propre


# ---------------------------------------------------------------------------
# Un appel par juge. Chacun renvoie (texte, modele, usage).
# ---------------------------------------------------------------------------

NOTES_FACTICES = {"B0": 3, "B1": 3, "B2": 3, "B3": 3, "B4": 2, "B5": 2, "B6": None}


def appel_factice(systeme: str, dialogue: str) -> tuple[str, str, dict]:
    """Reponse fixe, au contrat de sortie de la grille : notes constantes, justification
    qui le dit. Aucun appel reseau. Sert a tester la chaine de bout en bout sans cle."""
    ident = json.loads(dialogue).get("id")
    notes = {
        "id": ident,
        **NOTES_FACTICES,
        "justifications": {c: "juge factice : note fixe, aucun jugement" for c in CRITERES},
    }
    return json.dumps(notes, ensure_ascii=False), "factice-v1", {"entree": 0, "sortie": 0}


def appel_factice_b(systeme: str, dialogue: str) -> tuple[str, str, dict]:
    """Second juge factice (mode demonstration) : les notes du factice, DECALEES de facon
    deterministe (aucun hasard) d'apres l'empreinte SHA-256 de l'identifiant du dialogue : pour
    chaque axe, -1, 0 ou +1, borne entre 1 et 4. Meme dialogue, meme decalage : la demonstration
    donne toujours le meme alpha. Demonstration du calcul entre deux juges factices, pas une mesure."""
    ident = json.loads(dialogue).get("id")
    empreinte = int(hashlib.sha256(str(ident).encode("utf-8")).hexdigest(), 16)
    notes = {"id": ident}
    for rang, c in enumerate(CRITERES):
        base = NOTES_FACTICES.get(c)
        decalage = (-1, 0, 1)[(empreinte >> (2 * rang)) % 3]
        notes[c] = None if base is None else min(4, max(1, base + decalage))
    notes["justifications"] = {
        c: "juge factice-b : note du factice decalee de facon deterministe, aucun jugement" for c in CRITERES
    }
    return json.dumps(notes, ensure_ascii=False), "factice-b-v1", {"entree": 0, "sortie": 0}


JUGES = {"factice": appel_factice, "factice-b": appel_factice_b}


def cout_usd(modele_demande: str, usage: dict) -> float:
    if modele_demande not in MODELES.values():
        raise ValueError("juge non simulé interdit")
    return 0.0


FOURNISSEUR_JUGE = {"factice": "factice", "factice-b": "factice"}


def suffixe_version(version: str) -> str:
    """Fichier de sortie par version de grille : rien pour v1.0 (fichiers historiques), « -v1.1 »
    ensuite. Une renotation dans une nouvelle grille n'ecrase jamais les notes de l'ancienne."""
    return "" if version == "grille-B-v1.0" else "-" + version.replace("grille-B-", "")


def dialogue_hors_notation(campagne: str, cle: str) -> tuple[str, str, str]:
    """(identifiant, fichier du run, dialogue JSON) pour un dialogue exclu de la notation, construit
    comme exporter_notation.py (tours : message de l'utilisateur, reponse de Noa nettoyee).
    Identifiant « HN-<persona>-<bras> » : marque a part, jamais confondu avec un code N01..N09."""
    runs = sorted((RUNS / campagne).glob(f"{campagne}-*-{cle}.json"))
    if not runs:
        sys.exit(f"[X] aucun run {campagne}-*-{cle}.json")
    run = json.loads(runs[-1].read_text(encoding="utf-8"))
    tours = [
        {"n": t["n"], "utilisateur": t["persona"], "noa": t.get("noa") if t.get("noa") is not None else ""}
        for t in run["tours"]
        if t.get("persona") is not None
    ]
    ident = f"HN-{cle}"
    texte = json.dumps({"id": ident, "tours": tours}, ensure_ascii=False, indent=2)
    dossier = RUNS / campagne / "notation" / "hors_notation"
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / f"{ident}.json").write_text(texte, encoding="utf-8")
    return ident, runs[-1].name, texte


def _version_au_moins(version: str, mini: tuple[int, int]) -> bool:
    m = re.search(r"v(\d+)\.(\d+)$", version)
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= mini


def avec_fiche(version: str) -> bool:
    """Depuis la grille v1.2, le juge recoit aussi deux champs de la fiche du persona."""
    return _version_au_moins(version, (1, 2))


def axes_nullables(version: str, dialogue: str) -> tuple[str, ...]:
    """Axes qui peuvent etre null pour CE dialogue : B6 toujours ; B4 et B5 en grille v1.3 et plus,
    si le dialogue a moins de TOURS_MIN_B4_B5 tours (le juge voit les memes tours)."""
    if _version_au_moins(version, (1, 3)) and len(json.loads(dialogue).get("tours") or []) < TOURS_MIN_B4_B5:
        return ("B4", "B5", "B6")
    return ("B6",)


def exemples_du_prompt() -> set[str]:
    """Runs cites en exemple dans la consigne (lignes « - Exemple : <fichier> » de l'en-tete) :
    exclus de la notation dans cette grille, le juge ayant vu leur note de reference."""
    return set(re.findall(r"^- Exemple : (\S+\.json)\s*$", PROMPT_FICHIER.read_text(encoding="utf-8"), re.M))


def ajouter_fiche(campagne: str, fichier_run: str | None, texte: str) -> str:
    """Insere apres l'identifiant cas_sensible et ne_dira_pas_sans_confiance, tels que copies dans
    le fichier du run (la fiche au moment du dialogue, pas la fiche courante)."""
    if not fichier_run:
        sys.exit("[X] grille avec fiche : fichier du run inconnu pour un dialogue (correspondance.json)")
    persona = json.loads((RUNS / campagne / fichier_run).read_text(encoding="utf-8"))["persona"]
    d = json.loads(texte)
    fiche = {
        "cas_sensible": persona.get("cas_sensible") or "aucun",
        "ne_dira_pas_sans_confiance": persona.get("ne_dira_pas_sans_confiance") or [],
    }
    # un test a entree fixe garde son contexte fixe (exporter_notation.py) ; un dialogue n'en a pas
    contexte = {"contexte": d["contexte"]} if d.get("contexte") else {}
    return json.dumps({"id": d["id"], "fiche": fiche, **contexte, "tours": d["tours"]}, ensure_ascii=False, indent=2)


def cout_moyen_passe(campagne: str, j: str, version: str | None = None) -> float | None:
    """Cout moyen d'un dialogue pour ce juge, d'apres ses sorties deja payees dans la campagne,
    dans la meme grille si elle est donnee (la consigne v1.2 est plus longue que la v1.1)."""
    couts = []
    for f in (RUNS / campagne / "juges" / j).glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        c = d.get("cout_usd")
        if version and d.get("version_prompt") != version:
            continue
        if isinstance(c, (int, float)) and c > 0:
            couts.append(c)
    return sum(couts) / len(couts) if couts else None


def cout_deja_paye(campagne: str, juges: list[str]) -> float:
    """Cout deja paye dans la campagne par ces juges : somme des cout_usd des sorties deja ecrites (les fichiers que
    cout_moyen_passe relit deja pour le plafond, toutes grilles et passages), pour le cout cumule en direct. Seul le
    champ cout_usd est retenu ; une sortie illisible n'est pas comptee."""
    total = 0.0
    for j in juges:
        for f in (RUNS / campagne / "juges" / j).glob("*.json"):
            try:
                c = json.loads(f.read_text(encoding="utf-8")).get("cout_usd")
            except (OSError, ValueError) as e:
                print(f"  [!] {f.name} illisible ({type(e).__name__}) : non compte dans le cout cumule en direct")
                continue
            if isinstance(c, (int, float)) and c > 0:
                total += c
    return round(total, 6)


def main() -> int:
    """Un seul lanceur des juges a la fois sur la campagne (parallele.verrou_lanceur : une relance pendant que
    l'ancien processus tourne encore renoterait ses notations en cours), puis noter_campagne."""
    exiger_simulation()
    args = sys.argv[1:]
    if "--campagne" not in args or args.index("--campagne") + 1 >= len(args):
        sys.exit("[X] --campagne CXX obligatoire")
    campagne = campagne_valide(args[args.index("--campagne") + 1])
    with verrou_lanceur(RUNS / campagne / "_lanceur-juges.lock"), reseau_interdit():
        return noter_campagne()


def noter_campagne() -> int:
    global PROMPT_FICHIER
    args = sys.argv[1:]
    if "--grille" in args:
        # noter dans une grille archivee (ex. docs/evaluation/grilles/PROMPT-JUGE-v1.1.md) sans toucher a la courante
        i = args.index("--grille")
        PROMPT_FICHIER = Path(args[i + 1]).resolve()
        del args[i : i + 2]
    cout_estime = {}
    if "--cout-estime" in args:
        # cout par dialogue a utiliser quand la campagne n'a encore rien paye dans cette grille
        i = args.index("--cout-estime")
        cout_estime = {k: float(v) for k, v in (x.split("=") for x in args[i + 1].split(","))}
        del args[i : i + 2]
    if "--campagne" not in args:
        sys.exit("[X] --campagne CXX obligatoire")
    campagne = args[args.index("--campagne") + 1]
    del args[args.index("--campagne") : args.index("--campagne") + 2]
    juges = list(JUGES)
    if "--juges" in args:
        i = args.index("--juges")
        juges = args[i + 1].split(",")
        del args[i : i + 2]
    passage = 1
    if "--passage" in args:
        i = args.index("--passage")
        passage = int(args[i + 1])
        del args[i : i + 2]
    plafond = None
    if "--plafond" in args:
        i = args.index("--plafond")
        plafond = float(args[i + 1])
        del args[i : i + 2]
    hors_notation = []
    while "--hors-notation" in args:
        i = args.index("--hors-notation")
        hors_notation.append(args[i + 1])
        del args[i : i + 2]
    refaire = "--refaire" in args
    if refaire:
        args.remove("--refaire")
    sec = "--dry-run" in args
    if sec:
        args.remove("--dry-run")
    paralleles = 1
    if "--paralleles" in args:
        i = args.index("--paralleles")
        paralleles = int(args[i + 1])
        del args[i : i + 2]
    if not 1 <= paralleles <= PARALLELES_MAX:
        sys.exit(f"[X] --paralleles : de 1 a {PARALLELES_MAX}")
    if any(a.startswith("--") for a in args):
        sys.exit("[X] option inconnue ou mode non supporté : " + ", ".join(args))
    voulus = set(args)
    for j in juges:
        if j not in JUGES:
            sys.exit(f"[X] juge inconnu : {j} (attendus : {', '.join(JUGES)})")

    dossier = RUNS / campagne / "notation" / "dialogues"
    fichier_corr = RUNS / campagne / "notation" / "correspondance.json"
    correspondance = json.loads(fichier_corr.read_text(encoding="utf-8"))["dialogues"] if fichier_corr.exists() else {}
    # (identifiant, fichier du run, texte envoye) : les dialogues notes, puis ceux hors notation demandes
    a_noter = [
        (p.stem, correspondance.get(p.stem, {}).get("fichier"), p.read_text(encoding="utf-8"))
        for p in sorted(dossier.glob("N*.json"))
        if not voulus or p.stem in voulus
    ]
    a_noter += [dialogue_hors_notation(campagne, cle) for cle in hors_notation]
    if not a_noter:
        sys.exit(f"[X] aucun dialogue dans {dossier} (python -m banc.juges.exporter_notation --campagne {campagne})")
    systeme = prompt_juge()
    version = version_prompt()
    suffixe = suffixe_version(version)
    if avec_fiche(version):
        exemples = exemples_du_prompt()
        ecartes = [ident for ident, fr, _ in a_noter if fr in exemples]
        if ecartes:
            print(f"Exemples de la consigne, non notes en {version} : {ecartes}")
        a_noter = [
            (ident, fr, ajouter_fiche(campagne, fr, texte)) for ident, fr, texte in a_noter if fr not in exemples
        ]
        if not a_noter:
            sys.exit("[X] il ne reste aucun dialogue a noter hors exemples de la consigne")

    if sec:
        print(f"DRY-RUN — {len(a_noter)} dialogue(s), juges {juges}, grille {version}, modeles {MODELES}")
        print("\n--- consigne systeme ---\n" + systeme)
        print("\n--- premier dialogue envoye tel quel ---\n" + a_noter[0][2][:1500])
        return 0

    def nom_sortie(ident: str) -> str:
        return f"{ident}{suffixe}.json" if passage == 1 else f"{ident}{suffixe}-p{passage}.json"

    def deja_note(j: str, ident: str, fichier_run: str | None) -> dict | None:
        f = RUNS / campagne / "juges" / j / nom_sortie(ident)
        if refaire or not f.exists():
            return None
        ancien = json.loads(f.read_text(encoding="utf-8"))
        ok = (
            ancien.get("notes") and ancien.get("fichier_run") == fichier_run and ancien.get("version_prompt") == version
        )
        return ancien["notes"] if ok else None

    # Estimation avant tout appel : cout moyen deja paye par dialogue, par juge, x dialogues a faire
    restants = {j: sum(1 for ident, fr, _ in a_noter if not deja_note(j, ident, fr)) for j in juges}
    estimations = {}
    par_dialogue = {}  # estimation d'une notation, reservee avant son demarrage (plafond par reservation)
    for j in juges:
        moyen = cout_moyen_passe(campagne, j, version) or cout_estime.get(j)
        estimations[j] = None if j.startswith("factice") else (round(moyen * restants[j] * 1.1, 4) if moyen else None)
        par_dialogue[j] = 0.0 if j.startswith("factice") or not moyen else moyen * 1.1
    print(f"Grille {version} · passage {passage} · {len(a_noter)} dialogue(s) · a faire : {restants}")
    print(
        f"Estimation (cout moyen deja mesure + 10 %) : {estimations}" + (f" · plafond {plafond} $" if plafond else "")
    )
    if plafond is not None:
        if any(e is None for j, e in estimations.items() if not j.startswith("factice") and restants[j]):
            sys.exit("[X] plafond demande mais cout moyen inconnu pour un juge : rien n'est lance.")
        if sum(e or 0 for e in estimations.values()) > plafond:
            sys.exit(
                f"[X] estimation {sum(e or 0 for e in estimations.values()):.4f} $ au-dessus du plafond : rien n'est lance."
            )

    # Notations a faire, juge par juge puis dialogue par dialogue ; celles deja faites dans cette
    # grille et ce passage ne sont pas refaites (pas de double facturation).
    taches: list[tuple[str, int]] = []
    notes_par_juge: dict[str, list[dict | None]] = {j: [None] * len(a_noter) for j in juges}
    for j in juges:
        (RUNS / campagne / "juges" / j).mkdir(parents=True, exist_ok=True)
        for k, (ident, fichier_run, _) in enumerate(a_noter):
            ancien = deja_note(j, ident, fichier_run)
            if ancien:
                print(f"  {j} {ident}  deja note ({version}, passage {passage}), non refait (--refaire pour forcer)")
                notes_par_juge[j][k] = ancien
            else:
                taches.append((j, k))
    print(
        f"\n{'=' * 72}\nJUGES {', '.join(f'{j} ({MODELES[j]})' for j in juges)} — {version} — "
        f"{len(taches)} notation(s), {paralleles} en parallele\n{'=' * 72}"
    )

    def noter(tache: tuple[str, int]) -> tuple[dict | None, float | None]:
        """Une notation : deux essais au plus, chaque essai en echec au journal (ajout seul), sortie ecrite."""
        j, k = tache
        ident, fichier_run, dialogue = a_noter[k]
        dossier_j = RUNS / campagne / "juges" / j
        debut = time.perf_counter()
        erreur = None
        brut, modele, usage, notes = "", MODELES[j], {}, None
        usage_total = {
            "entree": 0,
            "sortie": 0,
            "reflexion": 0,
            "en_cache": 0,
            "ecriture_cache": 0,
            "essais_factures": 0,
        }
        echecs = []  # chaque essai en echec, delai depasse compris, garde dans la sortie
        fournisseur = FOURNISSEUR_JUGE[j]
        for essai in range(1, 3):
            verifier_interruption()  # lanceur interrompu : aucun nouvel essai
            if essai > 1:
                envoyer_direct("reprises", fournisseur=fournisseur, etape="juge")
            debut_essai = time.perf_counter()
            repondu = False
            try:
                brut, modele, usage = JUGES[j](systeme, dialogue)
                repondu = True
                compter_appel(fournisseur, "juge", duree_s=time.perf_counter() - debut_essai)
                # un essai qui a repondu est facture, meme si sa reponse est rejetee ensuite
                usage_total["essais_simules"] = essai
                for c in ("entree", "sortie", "reflexion", "en_cache", "ecriture_cache"):
                    usage_total[c] += usage.get(c) or 0
                notes = valider(extraire_json(brut), ident, axes_nullables(version, dialogue))
                break
            except Exception as e:  # noqa: BLE001 — on consigne, on ne plante pas la serie
                erreur = f"essai {essai} : {type(e).__name__} : {str(e)[:300]}"
                delai = any(m in f"{type(e).__name__} {e}" for m in ("Timeout", "timed out", "DEADLINE", "504"))
                if repondu:  # reponse recue mais rejetee (JSON ou notes invalides) : erreur « autre »
                    envoyer_direct("erreurs_autres", fournisseur=fournisseur, etape="juge")
                else:
                    compter_appel(fournisseur, "juge", erreur=f"{type(e).__name__} {e}")
                echecs.append({"essai": essai, "delai_depasse": delai, "erreur": erreur})
                print(f"  {j} {ident} [!] {'delai de ' + str(DELAI_MAX_S) + ' s depasse, ' if delai else ''}{erreur}")
                attendre_interruptible(3)
        duree = round(time.perf_counter() - debut, 1)
        if usage.get("niveau_reflexion"):
            usage_total["niveau_reflexion"] = usage["niveau_reflexion"]
        cout = (
            cout_usd(MODELES[j], usage_total)
            if usage_total["essais_factures"]
            else (0.0 if j.startswith("factice") else None)
        )
        (dossier_j / nom_sortie(ident)).write_text(
            json.dumps(
                {
                    "id": ident,
                    "fichier_run": fichier_run,
                    "hors_notation": ident.startswith("HN-"),
                    "juge": j,
                    "modele": modele,
                    "modele_demande": MODELES[j],
                    "version_prompt": version,
                    "passage": passage,
                    "horodatage_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "duree_s": duree,
                    "usage": usage_total,
                    "cout_usd": cout,
                    "notes": notes,
                    "erreur": erreur if notes is None else None,
                    "echecs_essais": echecs,
                    "reponse_brute": brut,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if notes:
            envoyer_direct("notations", fournisseur=fournisseur)  # le nombre seulement, jamais une note
            print(
                f"  {j} {ident}  "
                + "  ".join(f"{c}={notes[c] if notes[c] is not None else '–'}" for c in CRITERES)
                + f"  ({duree} s, entree {usage_total['entree']}, sortie {usage_total['sortie']}, "
                f"reflexion {usage_total['reflexion']}, {cout if cout is not None else '?'} $)"
            )
        else:
            print(f"  {j} {ident}  ECHEC : {erreur}")
        return notes, cout

    # A 1 worker : arret apres une notation si la depense ou sa projection depasse le plafond (controle_sequentiel).
    # A plusieurs : plafond par reservation (parallele.Plafond) ; un cout inconnu garde la reservation comme depense.
    budget = Plafond(plafond, Counter(j for j, _ in taches), estimations=par_dialogue) if paralleles > 1 else None
    reserves: dict[int, float] = {}
    couts_reels: list[float] = []
    faites: set[int] = set()
    depense_juge: Counter[str] = Counter()
    faites_juge: Counter[str] = Counter()
    juge_arrete: list[str] = []  # a 1 worker : juge dont la notation a declenche l'arret (pas de notes)

    def controle_sequentiel(rang_fini: int) -> str | None:
        """Le controle que faisait la boucle sequentielle apres la notation `rang_fini`."""
        j = taches[rang_fini][0]
        projection = (
            sum(couts_reels)
            + depense_juge[j] / faites_juge[j] * (restants[j] - faites_juge[j])
            + sum(estimations[k] or 0 for k in juges[juges.index(j) + 1 :])
        )
        if sum(couts_reels) > plafond or projection > plafond:
            juge_arrete.append(j)
            motif = (
                f"plafond {plafond} $ : depense {sum(couts_reels):.4f} $, projection {projection:.4f} $. "
                "Rien d'autre n'est lance."
            )
            print(f"\n[ARRET] {motif}")
            return motif
        return None

    def autoriser(rang: int) -> str | None:
        if paralleles == 1:
            return controle_sequentiel(rang - 1) if plafond is not None and rang > 0 else None
        montant, motif = budget.reserver(taches[rang][0])
        if motif:
            print(f"\n[ARRET] {motif}")
            return motif
        reserves[rang] = montant
        return None

    def terminer(rang: int, resultat: tuple[dict | None, float | None] | None, erreur: Exception | None) -> None:
        j, k = taches[rang]
        if erreur is not None:
            if budget is not None:
                budget.liberer(j, reserves.pop(rang), None)
            raise erreur  # hors appel au juge (ecriture de la sortie...) : arret
        notes, cout = resultat
        if budget is not None:
            budget.liberer(j, reserves.pop(rang), cout)
        couts_reels.append(cout or 0)
        depense_juge[j] += cout or 0
        faites_juge[j] += 1
        notes_par_juge[j][k] = notes
        faites.add(rang)
        envoyer_cout()

    def envoyer_cout() -> None:
        """En direct : cout cumule des juges dans la campagne (deja paye avant cette execution, plus cette execution :
        une relance ne le remet pas a zero). Le plafond --plafond porte sur l'execution, comme avant : la part du
        plafond (celle de l'alerte a 80 %) est la depense de l'execution divisee par ce plafond."""
        envoyer_direct("cout_usd", round(deja_paye + sum(couts_reels), 6), etape="juges")
        if plafond is not None:
            envoyer_direct("plafond_usd", plafond, etape="juges")
            envoyer_direct("part_plafond", round(sum(couts_reels) / plafond, 6) if plafond > 0 else 1, etape="juges")

    deja_paye = cout_deja_paye(campagne, juges)
    definir_campagne_direct(campagne)
    envoyer_cout()
    debut_juges = time.monotonic()
    _, arret = executer(
        taches, paralleles, noter, autoriser=autoriser, terminer=terminer, jauge=f"banc.en_cours.juges.{campagne}"
    )
    if paralleles == 1 and plafond is not None and arret is None and taches:
        arret = controle_sequentiel(len(taches) - 1)  # la derniere notation est controlee aussi
    if faites:  # rien n'a tourne : la duree d'une vraie execution n'est pas ecrasee
        envoyer_jauge(f"banc.duree_etape.juges.{campagne}.{paralleles}", round(time.monotonic() - debut_juges, 1))
    depense = sum(couts_reels)

    for j in juges:
        if j in juge_arrete or any(jj == j and rang not in faites for rang, (jj, _) in enumerate(taches)):
            continue  # juge non termine ou arrete au plafond : pas de fichier de notes
        notes_j = [n for n in notes_par_juge[j] if n]
        nom_notes = f"notes_{j}{suffixe}.json" if passage == 1 else f"notes_{j}{suffixe}_p{passage}.json"
        (RUNS / campagne / "juges" / nom_notes).write_text(
            json.dumps(
                {"juge": j, "modele": MODELES[j], "campagne": campagne, "version_prompt": version, "notes": notes_j},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            f"  -> {j} : {len(notes_j)}/{len(a_noter)} notes ({version}, passage {passage}) dans runs/{campagne}/juges/"
        )
        # effectifs par axe (grille v1.3 : B4 et B5 ne sont plus notes partout) : nombre de notes / sans objet
        print(
            "     effectifs : "
            + "  ".join(
                f"{c} {sum(1 for n in notes_j if n.get(c) is not None)}/{sum(1 for n in notes_j if n.get(c) is None)}"
                for c in CRITERES
            )
            + "  (notes / sans objet)"
        )
    print(f"\nCout total de cette execution : {depense:.6f} $" + (f" (plafond {plafond} $)" if plafond else ""))
    invalides = any(n is None for notes in notes_par_juge.values() for n in notes)
    return 1 if arret or invalides else 0


if __name__ == "__main__":
    sys.exit(main())

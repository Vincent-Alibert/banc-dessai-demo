"""Vrai lanceur du banc, limité aux doubles enregistrés du jeu synthétique C01.
Workers, limites partagées, pauses 429, budget simulé, réservations et reprises sont conservés.
Aucun client applicatif ou LLM ne fait partie de cette distribution.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import time
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from banc import chemins
from banc.campagnes import campagnes
from banc.dialogues import contrats as etat_simulation
from banc.personas.fiche_banc import FicheBanc, lire_toutes, plafond_mots
from banc.personas import reponses as moteur_persona
from banc.personas.reponses import MODELE as MODELE_PERSONA, VERSIONS_PERSONA, definir_graine, normaliser, repondre
from banc.campagnes.parallele import (
    PARALLELES_MAX,
    LimiteDebit,
    Pause429,
    Plafond,
    compter_appel,
    compter_exception,
    definir_campagne_direct,
    envoyer_direct,
    envoyer_jauge,
    executer,
    verifier_interruption,
    verrou_lanceur,
)
from banc.dialogues.contrats import (
    PorteCookies,
    RefusStrict,
    base_ciblee,
    commit_application,
    forger_cookies,
    definir_intervalle_noa,
    jouer_un_tour,
    modeles_non_metres,
    recuperer_contexte_utilisateur,
    inscrire,
    onboarder,
    quetes_actives,
    bouton_accueil,
    fermer_session,
    attendre_metrologie,
    attendre_analyse,
    couts,
    modeles_factures,
)
from banc.garde import exiger_simulation, campagne_valide, source_autorisee, nombre_positif, personas_valides, PERSONAS


def _enrichir_simulation(run):
    raise PermissionError("simulation non installée : écriture du run refusée")


def _restaurer_simulation():
    return None


# Version du persona d'une campagne neuve
VERSION_PERSONA_DEFAUT = "v1"
REGLES_RESUMEES = {"v1": "messages enregistrés du jeu synthétique C01 ; aucune génération"}
SCHEMA = "noa-banc-transcript/0.5"
BRAS = ("sans_onboarding", "avec_onboarding")
DOSSIER_RUNS = chemins.RUNS

# Ces deux-la ouvrent par le bouton d'accueil.
OUVERTURE_BOUTON = {"jeanpierre", "nadia"}

# Dialogues en parallele : limites PARTAGEES par tous les workers du lanceur.
LIMITE_ONBOARDING = LimiteDebit(5, 60.0)  # /api/onboarding/analyze : 5 par minute
PAUSE_MISTRAL = Pause429(base_s=10.0, plafond_s=60.0)  # apres un 429 de Mistral : 10, 20, 40 puis 60 s
REPRISES_429_MAX = 3  # reprises d'un meme tour ; au-dela, erreur
MARQUE_429_MISTRAL = "Mistral API error 429"  # tel que /api/oracle le renvoie dans « event: error »
# Simulation exclusivement : les emplacements d’appels sont remplis par les doubles enregistrés.
BRAS_BANC = "simulation"
# Reservation de chaque dialogue parallele, retiree quand son run est ecrit. Un dialogue en erreur, interrompu
# ou tue laisse la sienne : sa depense inconnue reste comptee par depense_campagne aux executions suivantes.
DOSSIER_RESERVATIONS = "_reservations"
FICHIER_VERROU = "_lanceur-dialogues.lock"  # parallele.verrou_lanceur : un lanceur par campagne
# reservation illisible (fichier tronque) : comptee a l'estimation par defaut la plus haute du DAG
# (p90 du cout Noa mesure par dialogue : 0,0315 $)
RESERVATION_ILLISIBLE_USD = 0.032

# ---------------------------------------------------------------------------
# Ce que fait le client, reproduit a l'identique
# ---------------------------------------------------------------------------


def nettoyer_comme_le_client(texte: str) -> str:
    """useOracleSSE.ts:566-573 et noa-page.utils.ts:17-26 : les trois jetons retires,
    les triples sauts de ligne reduits, le tout sans espaces aux bords."""
    t = re.sub(r"\[QUEST_CREATE:[^\]]*\]", "", texte, flags=re.I)
    t = re.sub(r"\[VOYAGE_SUGGEST:[^\]]*\]", "", t, flags=re.I)
    t = re.sub(r"\[FLASH:[^\]]*\]", "", t, flags=re.I)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def jouer_un_tour_avec_reprises(
    messages: list[dict], porte: PorteCookies, n: int, contexte: dict, extra: dict, reprises: list[dict]
) -> tuple[str, float, dict]:
    """jouer_un_tour, repris apres un 429 de Mistral (flux de /api/oracle en « event: error ») : le meme tour,
    meme historique, apres la pause COMMUNE a tous les workers (PAUSE_MISTRAL), au plus REPRISES_429_MAX fois.
    Chaque reprise est ajoutée à `reprises`, consignée dans le tour. Dans cette distribution,
    aucun essai n’est facturé : les réponses proviennent uniquement des doubles enregistrés.
    Toute autre erreur, ou un 429 après la dernière reprise, est relevée comme avant."""
    while True:
        PAUSE_MISTRAL.attendre()  # pause 429 commune, interruptible
        verifier_interruption()  # apres la pause ; jouer_un_tour reverifie apres l'attente d'intervalle
        try:
            resultat = jouer_un_tour(messages, porte, n, contexte, extra)
        except RefusStrict as e:
            compter_appel("mistral", "noa", erreur=str(e))  # en direct : l'essai et son erreur
            if MARQUE_429_MISTRAL not in str(e) or len(reprises) >= REPRISES_429_MAX:
                raise
            envoyer_direct("reprises", fournisseur="mistral", etape="noa")
            attente = PAUSE_MISTRAL.signaler()
            reprises.append(
                {
                    "horodatage_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "attente_s": attente,
                    "erreur": str(e)[:300],
                }
            )
            print(
                f"  [!] 429 de Mistral au tour {n} : reprise {len(reprises)}/{REPRISES_429_MAX} dans {attente:.0f} s "
                "(pause commune a tous les workers)"
            )
            continue
        except Exception as e:
            compter_exception("mistral", "noa", e)  # delai depasse, connexion coupee... : releve
            raise
        PAUSE_MISTRAL.succes()
        compter_appel("mistral", "noa", duree_s=resultat[1])
        return resultat


def compter_persona(rep: moteur_persona.Reponse) -> None:
    """Metriques en direct d'un message du persona (un appel NVIDIA vu du lanceur ; regenerations et relance de la
    decision ne sont pas comptees a part) : chaque 429 recu, ses reprises, puis l'erreur HTTP finale ou la latence."""
    refus = rep.refus or ""
    for _ in range(rep.tentatives_429):
        compter_appel("nvidia", "persona", erreur="HTTP 429")
    reprises = rep.tentatives_429 - (1 if refus.startswith("429") else 0)  # « 429 persistant » : pas de reprise
    if reprises > 0:
        envoyer_direct("reprises", reprises, fournisseur="nvidia", etape="persona")
    if refus.startswith("HTTP "):
        compter_appel("nvidia", "persona", erreur=refus)
    elif not refus.startswith("429"):
        compter_appel("nvidia", "persona", duree_s=rep.duree_s)


# ---------------------------------------------------------------------------
# Fermeture, couts, estimation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Un dialogue
# ---------------------------------------------------------------------------


def fin_du_dialogue(tours: list[dict], nb_tours: int, depart: dict | None) -> tuple[str, str | None, int]:
    """(statut_fin, motif_fin, tour_fin). tour_fin = nombre de reponses de Noa recues ; un depart du
    persona v2 l'emporte ; sinon MAX_TURNS si tous les tours ont une reponse, TECHNICAL_ERROR sinon."""
    tour_fin = sum(1 for t in tours if t.get("noa"))
    if depart:
        return depart["statut"], depart.get("motif"), tour_fin
    if tour_fin >= nb_tours:
        return "MAX_TURNS", None, tour_fin
    dernier = tours[-1] if tours else {}
    motif = (
        dernier.get("refus_persona")
        or (dernier.get("erreur_oracle") or "")[:200]
        or ("reponse de Noa vide" if dernier.get("noa") == "" else "dialogue interrompu")
    )
    return "TECHNICAL_ERROR", motif, tour_fin


def compter_decisions(tours: list[dict], depart: dict | None, version: str | None = None) -> dict:
    """Formes des decisions du persona v2 sur le dialogue (depart compris) : stricte, toleree, relance
    (obtenue par la relance), repli (restant apres relance) ; relances = relances faites, reussies ou non."""
    decisions = [
        (t.get("persona_decision_forme"), t.get("persona_decision_relance"))
        for t in tours
        if t.get("persona_decision_forme")
    ]
    if depart and depart.get("decision_forme"):
        decisions.append((depart["decision_forme"], depart.get("decision_relance")))
    formes = [f for f, _ in decisions]
    compte = {
        "total": len(decisions),
        **{f: formes.count(f) for f in ("stricte", "toleree", "relance", "repli")},
        "relances": sum(1 for _, r in decisions if r),
    }
    if version == "v2.2":
        # persona v2.2 seulement (les runs v2 et v2.1 restent tels quels) : QUIT_NEGATIVE incoherents avec
        # leur message, traites comme CONTINUE ; ils sont aussi comptes dans les formes ci-dessus
        compte["departs_incoherents"] = sum(1 for t in tours if t.get("persona_depart_incoherent"))
        # depart ambigu (dialogue arrete, pas un abandon) : 0 ou 1
        compte["departs_ambigus"] = 1 if depart and depart.get("categorie") == "ambigu" else 0
    return compte


def dialoguer(
    fiche: FicheBanc,
    horodatage: str,
    nb_tours: int,
    campagne: str,
    bras: str,
    version_persona: str = "v1",
    version_noa: str | None = None,
    paralleles: int = 1,
) -> dict:
    from banc.dialogues.simulation import GARDE_RESEAU

    if not GARDE_RESEAU["active"]:
        raise PermissionError("lanceur : garde réseau de simulation inactive")
    ident = fiche.identifiant
    debut_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prenom = fiche.fiche.identite.prenom
    print(
        f"\n{'=' * 72}\n{ident} · {bras} — {prenom}, {fiche.fiche.identite.age} ans · "
        f"{fiche.axes.position} · {fiche.axes.cas_sensible} · pilier {fiche.pilier_dominant}\n{'=' * 72}"
    )

    email = f"{ident}.{bras.split('_')[0]}.{horodatage.lower()}@simulation.invalid"
    username = f"{ident}_{bras[:1]}_{horodatage[-6:].lower()}"
    session = inscrire(email, secrets.token_urlsafe(18), username, prenom)
    user_id = session["user"]["id"]
    porte = PorteCookies(forger_cookies(session))
    print(f"  compte {email} · {user_id[:8]}…")

    onboarding: dict | None = None
    attente_onboarding = 0.0
    if bras == "avec_onboarding":
        attente_onboarding = LIMITE_ONBOARDING.attendre()  # 5 par minute, tous workers confondus
        if attente_onboarding:
            print(f"  onboarding : {attente_onboarding:.0f} s d'attente (limite de 5 par minute, partagee)")
        try:
            onboarding = onboarder(fiche, porte)  # verifie l'interruption avant chaque POST
        except Exception as e:
            compter_exception("mistral", "onboarding", e)  # refus HTTP, delai depasse... (pas l'interruption)
            raise
        # latence de l'onboarding : de la premiere requete a la reponse (attente apres un 429 comprise)
        compter_appel("mistral", "onboarding", duree_s=onboarding["duree_s"])
        r = onboarding["reponse"]
        print(
            f"  onboarding en {onboarding['duree_s']} s : "
            f"{(r.get('journey') or {}).get('title') or r.get('status') or 'reponse sans titre'}"
        )

    contexte = recuperer_contexte_utilisateur(porte)
    quetes = quetes_actives(user_id, session["access_token"])
    print(f"  userContext : quetes au total {contexte.get('totalQuestsEver')} · actives envoyees {len(quetes)}")
    etat_simulation.CONVERSATION["id"] = None

    ouverture = "bouton" if ident in OUVERTURE_BOUTON else "message"
    tours: list[dict] = []
    historique_persona: list[dict] = []  # vu du persona : Noa = user, persona = assistant
    messages_noa: list[dict] = []  # vu de Noa    : persona = user, Noa = assistant
    refus: str | None = None
    depart: dict | None = None  # persona v2 : END_NORMAL ou QUIT_NEGATIVE, hors du message
    t_debut = time.monotonic()

    for n in range(1, nb_tours + 1):
        rep_persona = None
        extra: dict = {"activeQuests": quetes}
        if n == 1 and ouverture == "bouton":
            texte_persona = bouton_accueil(porte)
            extra["isQuickAction"] = True
        elif n == 1:
            texte_persona, _ = normaliser(fiche.fiche.etat_entree.premier_message, fiche)
            extra["inputMethod"] = "keyboard"
        else:
            try:
                rep_persona = repondre(fiche, historique_persona, version=version_persona)  # verifie avant chaque appel
            except Exception as e:
                compter_exception("nvidia", "persona", e)  # delai depasse du SDK... (pas l'interruption)
                raise
            compter_persona(rep_persona)
            if rep_persona.decision in ("END_NORMAL", "QUIT_NEGATIVE") and not rep_persona.refus:
                # Depart apres la (n-1)e reponse de Noa : rien n'est envoye, AUCUN tour n'est ajoute ;
                # le dernier appel au persona est garde ici (jetons comptes dans fait_cout).
                depart = {
                    "statut": rep_persona.decision,
                    "motif": rep_persona.motif,
                    "apres_tour": n - 1,
                    "decision_forme": rep_persona.decision_forme,
                    "decision_brute": rep_persona.decision_brute,
                    "decision_relance": rep_persona.decision_relance,
                    "decision_brute_relance": rep_persona.decision_brute_relance,
                    "message_non_envoye": rep_persona.message_non_envoye,
                    "persona_duree_s": round(rep_persona.duree_s, 2),
                    "persona_attente_debit_s": rep_persona.attente_debit_s,
                    "persona_tokens_entree": rep_persona.tokens_entree,
                    "persona_tokens_sortie": rep_persona.tokens_sortie,
                }
                if version_persona == "v2.2" and rep_persona.decision == "QUIT_NEGATIVE":
                    # categorie du depart (coherence_depart) : « coherent » = abandon ; « ambigu » = dialogue
                    # arrete mais pas un abandon, liste a relire (champs absents des runs v2 et v2.1)
                    depart["categorie"] = rep_persona.depart_categorie
                    depart["categorie_raison"] = rep_persona.depart_categorie_raison
                print(
                    f"  [fin] {prenom} part apres la reponse {n - 1} de Noa : {rep_persona.decision}"
                    f"{' — ' + rep_persona.motif if rep_persona.motif else ''} ({rep_persona.decision_forme})"
                    + (
                        " — depart AMBIGU, pas compte comme abandon, a relire"
                        if depart.get("categorie") == "ambigu"
                        else ""
                    )
                )
                break
            if rep_persona.refus:
                refus = f"tour {n} : {rep_persona.refus}"
                print(f"  [!] refus du modele persona au tour {n} : {rep_persona.refus} — dialogue arrete")
                tours.append(
                    {
                        "n": n,
                        "persona": None,
                        "noa": None,
                        "refus_persona": rep_persona.refus,
                        "persona_duree_s": round(rep_persona.duree_s, 2),
                    }
                )
                break
            texte_persona = rep_persona.texte
            extra["inputMethod"] = "keyboard"
            if rep_persona.depart_incoherent:
                print(
                    f"  [!] {prenom} : QUIT_NEGATIVE incoherent avec son message ({rep_persona.depart_incoherent_raison})"
                    " — traite comme CONTINUE, le message part chez Noa"
                )

        messages_noa.append({"role": "user", "content": texte_persona})
        etiquette = "bouton" if (n == 1 and ouverture == "bouton") else prenom
        print(f"\n  Tour {n} — {etiquette} : {texte_persona[:150]}{'…' if len(texte_persona) > 150 else ''}")
        reprises: list[dict] = []  # reprises apres un 429 de Mistral (vide le plus souvent)
        try:
            brut, duree_noa, ent = jouer_un_tour_avec_reprises(messages_noa, porte, n, contexte, extra, reprises)
        except RefusStrict as e:
            refus = f"tour {n} : /api/oracle a refuse — {str(e).splitlines()[0]}"
            print(f"  [X] {refus}")
            tours.append(
                {
                    "n": n,
                    "persona": texte_persona,
                    "noa": None,
                    "erreur_oracle": str(e)[:300],
                    "noa_reprises_429": reprises,
                }
            )
            break
        propre = nettoyer_comme_le_client(brut)
        messages_noa.append({"role": "assistant", "content": propre})
        historique_persona += [{"role": "assistant", "content": texte_persona}, {"role": "user", "content": propre}]
        print(
            f"         Noa ({duree_noa:.1f} s, mana {ent.get('X-Oracle-Remaining')}) : "
            f"{propre[:150].replace(chr(10), ' ')}{'…' if len(propre) > 150 else ''}"
        )
        tours.append(
            {
                "n": n,
                "persona": texte_persona,
                "noa": propre,
                "noa_brut": brut if brut != propre else None,
                "ouverture": ouverture if n == 1 else None,
                "noa_duree_s": round(duree_noa, 2),
                "noa_caracteres": len(propre),
                "noa_modele_entete": ent.get("X-Oracle-Model"),
                "noa_modele_facture": None,
                "noa_tier": ent.get("X-Oracle-Tier"),
                "mana_restante": ent.get("X-Oracle-Remaining"),
                "safety": ent.get("X-Oracle-Safety"),
                "noa_reprises_429": reprises,
                "persona_duree_s": round(rep_persona.duree_s, 2) if rep_persona else None,
                "persona_attente_debit_s": rep_persona.attente_debit_s if rep_persona else None,
                "persona_tokens_entree": rep_persona.tokens_entree if rep_persona else None,
                "persona_tokens_sortie": rep_persona.tokens_sortie if rep_persona else None,
                "persona_raisonnement": rep_persona.raisonnement_present if rep_persona else None,
                "persona_coupe_au_plafond": rep_persona.coupe if rep_persona else None,
                "persona_derive_anglais": rep_persona.derive_anglais if rep_persona else None,
                "persona_mots": len(texte_persona.split()),
                # persona v2 : decision prise avec ce message (CONTINUE), et sa forme (stricte, toleree, repli)
                "persona_decision": rep_persona.decision if rep_persona else None,
                "persona_decision_forme": rep_persona.decision_forme if rep_persona else None,
                # relance de la seule decision (ligne DECISION absente ou illisible au premier appel)
                "persona_decision_relance": rep_persona.decision_relance if rep_persona else None,
                "persona_decision_brute_relance": rep_persona.decision_brute_relance if rep_persona else None,
            }
        )
        if version_persona == "v2.2" and rep_persona:
            # persona v2.2 : QUIT_NEGATIVE incoherent avec ce message, traite comme CONTINUE (coherence_depart) ;
            # champs absents des runs v2 et v2.1, qui restent tels quels
            tours[-1].update(
                {
                    "persona_depart_incoherent": rep_persona.depart_incoherent,
                    "persona_depart_incoherent_raison": rep_persona.depart_incoherent_raison,
                    "persona_depart_ecarte_motif": rep_persona.depart_ecarte_motif,
                    "persona_depart_ecarte_brute": rep_persona.depart_ecarte_brute,
                }
            )

    duree_totale = time.monotonic() - t_debut
    statut_fin, motif_fin, tour_fin = fin_du_dialogue(tours, nb_tours, depart)
    print(f"  fin : {statut_fin}{' — ' + motif_fin if motif_fin else ''} · tour_fin {tour_fin}")
    conv_id = etat_simulation.CONVERSATION["id"]
    statut_fermeture = fermer_session(porte, conv_id)
    tours_joues = sum(1 for t in tours if t.get("noa"))
    t_attente = time.monotonic()
    n_metres = attendre_metrologie(user_id, tours_joues)
    attente_metrologie = time.monotonic() - t_attente
    if n_metres < tours_joues:
        print(f"  [!] metrologie incomplete : {n_metres} main_response pour {tours_joues} tours joues")
    t_attente = time.monotonic()
    statut_analyse = attendre_analyse(conv_id)
    attente_analyse = time.monotonic() - t_attente
    if statut_analyse != "done":
        print(f"  [!] analyse d'apres-conversation en statut '{statut_analyse}' apres attente")
    c = couts(conv_id, user_id)
    c["tours_metres"] = n_metres
    for t, modele in zip([t for t in tours if t.get("noa")], modeles_factures(user_id)):
        t["noa_modele_facture"] = modele
    emb = {
        "hors_metrologie": True,
        "appels_estimes": 0,
        "tokens_estimes": 0,
        "cout_usd_estime": 0,
        "hypothese": "simulation : aucun embedding exécuté",
    }
    print(
        f"\n  fermeture {statut_fermeture} · dialogue {c['dialogue']['cout_usd']} $ · onboarding {c['onboarding']['cout_usd']} $ · "
        f"analyse {c['analyse']['cout_usd']} $ ({c['analyse']['file_oracle_analysis_queue']}) · "
        f"modeles factures {sorted({t['noa_modele_facture'] for t in tours if t.get('noa_modele_facture')})}"
    )

    run = {
        "schema": SCHEMA,
        "bras_banc": BRAS_BANC,
        "campagne": campagne,
        "bras": bras,
        "horodatage_utc": horodatage,
        "type_run": "dialogue",
        "version_persona": version_persona,
        # etiquette de la version de Noa servie (--version-noa) ; None : l'entrepot retombe sur la branche
        "version_noa": version_noa,
        # fin du dialogue : statut, motif (QUIT_NEGATIVE : celui du persona ; TECHNICAL_ERROR : l'erreur),
        # tour_fin = reponses de Noa recues ; depart = le dernier appel au persona v2, rien n'a ete envoye
        "statut_fin": statut_fin,
        "motif_fin": motif_fin,
        "tour_fin": tour_fin,
        "depart": depart,
        # empreinte de la fiche COMPLETE (pas seulement de ce resume) : l'entrepot s'en sert pour
        # sa dimension persona a evolution lente de type 2 (une ligne par version de fiche).
        "persona": {
            "identifiant": ident,
            "empreinte_fiche": hashlib.sha256(fiche.model_dump_json().encode("utf-8")).hexdigest()[:16],
            "prenom": prenom,
            "age": fiche.fiche.identite.age,
            "axes": fiche.axes.model_dump(),
            "pilier_dominant": fiche.pilier_dominant,
            "cas_sensible": str(fiche.axes.cas_sensible),
            "fiche_meta": fiche.fiche.meta.model_dump(),
            "ne_dira_pas_sans_confiance": fiche.fiche.etat_entree.ne_dira_pas_sans_confiance,
            # pour controles_persona.py : la longueur attendue et les declencheurs, tels qu'au run
            "longueur_moyenne_mots": fiche.fiche.style.longueur_moyenne_mots,
            "declencheurs_decrochage": fiche.fiche.declencheurs_decrochage,
        },
        "modele_persona": {
            "id": MODELE_PERSONA,
            "raisonnement": "coupe (enable_thinking=false)",
            "graine": moteur_persona.GRAINE,
            "penalites": "frequency 0.3 · presence 0.3 · repetition 1.05",
            "plafond_mots": plafond_mots(str(fiche.axes.style_ecriture)),
            "version_regles": version_persona,
            "regles_du_banc": REGLES_RESUMEES[version_persona],
        },
        # modele reellement servi a chaque etape : persona, reponse de Noa par tour,
        # et chaque type d'appel de oracle_metering (reformulation, micro-IAs, onboarding, analyse)
        "modeles_par_etape": {
            "persona": MODELE_PERSONA,
            "noa_par_tour": [t.get("noa_modele_facture") for t in tours if t.get("noa")],
            "metrologie": {d["request_type"]: d.get("modeles", []) for d in c["detail"]},
        },
        "cible": {
            "type": "double_enregistre",
            "application": commit_application(),
            "base": base_ciblee(),
            "conversation_id": conv_id,
            # etapes de Noa hors oracle_metering : modele configure dans le code (docs/exploitation/GOUVERNANCE.md §5)
            "modeles_non_metres": modeles_non_metres(),
        },
        "compte": {
            "email": email,
            "user_id": user_id,
            "tiers_servis": sorted({t["noa_tier"] for t in tours if t.get("noa_tier")}),
            "modeles_entete": sorted({t["noa_modele_entete"] for t in tours if t.get("noa_modele_entete")}),
            "modeles_factures": sorted({t["noa_modele_facture"] for t in tours if t.get("noa_modele_facture")}),
        },
        "onboarding": onboarding,
        "ouverture": ouverture,
        "requete": {
            "champs": [
                "messages",
                "userContext",
                "activeQuests",
                "conversationId",
                "inputMethod (saisie)",
                "isQuickAction (bouton)",
            ],
            "historique_noa": "nettoye comme le client (QUEST_CREATE, VOYAGE_SUGGEST, FLASH)",
        },
        "contexte_utilisateur": contexte,
        # Constats du produit observes dans ce run, jamais un constat fixe ecrit sans avoir ete observe.
        "constats": [],
        "tours": tours,
        "fermeture_session": {"http": statut_fermeture},
        "cout": {
            "dialogue": c["dialogue"],
            "onboarding": c["onboarding"],
            "analyse": c["analyse"],
            "embedding_estime": emb,
            "detail_metrologie": c["detail"],
            "non_metres_connus": c["non_metres_connus"],
            "tours_metres": n_metres,
            # simulation (campagnes S*, anciennes C9x) : couts a 0, cout simule a part, jamais compte comme mesure
            **{k: c[k] for k in ("simule_usd", "note") if k in c},
        },
        "resume": {
            "tours_joues": tours_joues,
            "tours_demandes": nb_tours,
            "statut_fin": statut_fin,
            "decisions_persona": compter_decisions(tours, depart, version_persona),
            "duree_totale_s": round(duree_totale, 1),
            "refus_persona": refus,
            "rotations_cookie": porte.rotations,
            # dialogues en parallele : lancement, horloge et attentes du dialogue, pour mesurer ce que le
            # parallelisme change (les durees des tours sont celles ci-dessus)
            "paralleles": paralleles,
            "debut_utc": debut_utc,
            "fin_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "attente_onboarding_s": attente_onboarding,
            "attente_metrologie_s": round(attente_metrologie, 1),
            "attente_analyse_s": round(attente_analyse, 1),
            "reprises_429": sum(len(t.get("noa_reprises_429") or []) for t in tours),
        },
    }
    _enrichir_simulation(run)
    dossier = DOSSIER_RUNS / campagne
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / f"{campagne}-{horodatage}-{ident}-{bras}.json"
    # ecriture atomique : fichier temporaire du meme dossier, puis remplacement ; un processus tue pendant
    # l'ecriture ne laisse jamais un run partiel sous le nom du run (la reservation reste alors comptee)
    temporaire = chemin.with_name(chemin.name + ".tmp")
    temporaire.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporaire, chemin)
    # dialogue parallele : le run porte le cout ; sa reservation est retiree ICI, dans le worker, apres
    # l'ecriture complete du run, meme si le lanceur a ete interrompu pendant la fin du dialogue
    fichier_reservation(campagne, horodatage, ident, bras).unlink(missing_ok=True)
    run["_fichier"] = chemin.name
    return run


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------


def lire_option(args: list[str], nom: str, defaut: str | None) -> str | None:
    if nom not in args:
        return defaut
    i = args.index(nom)
    if i + 1 >= len(args):
        sys.exit(f"[X] {nom} attend une valeur")
    v = args[i + 1]
    del args[i : i + 2]
    return v


def runs_existants(campagne: str) -> dict[tuple[str, str, str | None], str]:
    """(persona, bras, version_noa) -> fichier, pour les runs deja ecrits dans la campagne (dialogues payes).
    La version de Noa fait partie de la cle : deux versions dans une meme campagne
    ne se masquent pas a la reprise."""
    sortie = {}
    for f in sorted((DOSSIER_RUNS / campagne).glob(f"{campagne}-*.json")):
        try:
            run = json.loads(f.read_text(encoding="utf-8"))
            sortie[(run["persona"]["identifiant"], run["bras"], run.get("version_noa"))] = f.name
        except (ValueError, KeyError, TypeError):
            continue
    return sortie


def versions_persona_existantes(campagne: str) -> set[str]:
    """Versions du persona des runs de dialogue deja ecrits dans la campagne ; un run sans le champ est
    v1 (C00 a C05)."""
    versions = set()
    for f in (DOSSIER_RUNS / campagne).glob(f"{campagne}-*.json"):
        try:
            run = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(run, dict) and "persona" in run and run.get("type_run", "dialogue") == "dialogue":
            versions.add(run.get("version_persona") or "v1")
    return versions


def choisir_version_persona(campagne: str, demandee: str | None) -> str:
    """Sans option : la version deja utilisee dans la campagne (une campagne existante ne change pas de
    comportement, --reprendre compris), v1 pour ce jeu fourni. Jamais deux versions dans une campagne."""
    if demandee is not None and demandee not in VERSIONS_PERSONA:
        sys.exit(f"[X] --version-persona : {', '.join(VERSIONS_PERSONA)}")
    existantes = versions_persona_existantes(campagne)
    if len(existantes) > 1:
        sys.exit(f"[X] runs/{campagne} melange deja les versions du persona {sorted(existantes)} : a trier a la main")
    if existantes:
        (deja,) = existantes
        if demandee and demandee != deja:
            sys.exit(
                f"[X] runs/{campagne} contient des runs persona {deja} : pas de {demandee} dans la meme "
                "campagne (une nouvelle campagne pour une nouvelle version)"
            )
        return deja
    return demandee or VERSION_PERSONA_DEFAUT


def cout_noa(run: dict) -> float:
    """Cout Noa d'un run : dialogue + onboarding + analyse ; un run simule (simulation.py) compte son cout
    simule (cout.simule_usd), ses couts mesures etant a 0 : le plafond se verifie aussi en simulation."""
    c = run.get("cout") or {}
    if "simule_usd" in c:
        return c["simule_usd"]
    return sum((c.get(k) or {}).get("cout_usd") or 0 for k in ("dialogue", "onboarding", "analyse"))


def depense_campagne(campagne: str) -> tuple[float, int]:
    """(cout Noa, nombre de runs) : dialogue + onboarding + analyse de chaque run ecrit, plus les reservations
    laissees par des dialogues paralleles en erreur ou interrompus (runs/<C>/_reservations/, aucune en sequentiel :
    seuls les runs ecrits comptent), sauf celle dont le run du meme essai a ete lu."""
    total, n = 0.0, 0
    lus: set[str] = set()
    for f in (DOSSIER_RUNS / campagne).glob(f"{campagne}-*.json"):
        try:
            run = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        total += cout_noa(run)
        n += 1
        lus.add(f.name)
    for f in (DOSSIER_RUNS / campagne / DOSSIER_RESERVATIONS).glob("*.json"):
        if f.name in lus:
            # run du meme essai, lu sans erreur (tue entre l'ecriture du run et le retrait de la reservation) :
            # le run porte le cout, la reservation n'est pas comptee deux fois ; run illisible : elle reste comptee
            continue
        try:
            total += float(json.loads(f.read_text(encoding="utf-8"))["reserve_usd"])
        except (ValueError, KeyError, TypeError) as e:
            # fichier tronque ou illisible (lanceur tue pendant l'ecriture) : compte prudemment, jamais 0
            print(f"  [!] reservation illisible {f.name} ({type(e).__name__}) : comptee {RESERVATION_ILLISIBLE_USD} $")
            total += RESERVATION_ILLISIBLE_USD
    return round(total, 6), n


def fichier_reservation(campagne: str, horodatage: str, ident: str, bras: str) -> Path:
    """Fichier de reservation d'un dialogue parallele (meme nom que son run, dans runs/<C>/_reservations/)."""
    return DOSSIER_RUNS / campagne / DOSSIER_RESERVATIONS / f"{campagne}-{horodatage}-{ident}-{bras}.json"


def ecrire_reservation(campagne: str, horodatage: str, ident: str, bras: str, montant: float) -> Path:
    """Reservation d'un dialogue parallele, ecrite avant son demarrage (fil principal) ; retiree par dialoguer()
    quand le run est ecrit."""
    f = fichier_reservation(campagne, horodatage, ident, bras)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        json.dumps(
            {
                "persona": ident,
                "bras": bras,
                "reserve_usd": round(montant, 6),
                "horodatage_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        ),
        encoding="utf-8",
    )
    return f


def noter_echec(campagne: str, ident: str, bras: str, erreur: str) -> None:
    """Journal des dialogues en echec (runs/<C>/_echecs.json) : la reprise les retente."""
    f = DOSSIER_RUNS / campagne / "_echecs.json"
    # appele seulement depuis le fil principal (terminer de executer) : jamais deux reecritures en meme temps
    f.parent.mkdir(parents=True, exist_ok=True)
    journal = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    journal.append(
        {
            "persona": ident,
            "bras": bras,
            "erreur": erreur[:500],
            "horodatage_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    f.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default="simulation", choices=["simulation"])
    parser.add_argument("--campagne", required=True, type=campagne_valide)
    parser.add_argument("--simulation", type=Path, default=chemins.JEUX_DEMO / "C01")
    parser.add_argument("--tours", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--bras", choices=[*BRAS, "les_deux"], default="les_deux")
    parser.add_argument("--paralleles", type=int, choices=range(1, PARALLELES_MAX + 1), default=4)
    parser.add_argument("--echelle-delais", type=nombre_positif, default=0)
    parser.add_argument("--intervalle-noa", type=nombre_positif, default=0)
    parser.add_argument("--plafond-usd", type=nombre_positif)
    parser.add_argument("--cout-estime-dialogue", type=nombre_positif, default=0.04)
    parser.add_argument("--version-persona", choices=VERSIONS_PERSONA, default="v1")
    parser.add_argument("--version-noa", default="C01-enregistre")
    parser.add_argument("--graine", type=int)
    parser.add_argument("--reprendre", action="store_true")
    parser.add_argument("--personas", dest="selection_personas")
    parser.add_argument("personas", nargs="*")
    options = parser.parse_args()
    exiger_simulation(options.mode)
    if options.selection_personas and options.personas:
        parser.error("choisir --personas ou les identifiants positionnels, pas les deux")
    try:
        if options.selection_personas:
            options.personas = personas_valides(options.selection_personas)
        elif options.personas:
            options.personas = personas_valides(",".join(options.personas))
    except ValueError as exc:
        parser.error(str(exc))
    try:
        source = source_autorisee(options.simulation)
    except ValueError as exc:
        parser.error(str(exc))
    campagne = options.campagne
    if campagnes.a_des_runs(campagne, DOSSIER_RUNS) and not options.reprendre:
        parser.error("campagne déjà écrite : utiliser --reprendre ou un nouveau code S*")
    args = [
        "--tours",
        str(options.tours),
        "--bras",
        options.bras,
        "--paralleles",
        str(options.paralleles),
        "--simulation",
        str(source),
        "--echelle-delais",
        str(options.echelle_delais),
        "--intervalle-noa",
        str(options.intervalle_noa),
        "--version-persona",
        options.version_persona,
        "--version-noa",
        options.version_noa,
        "--cout-estime-dialogue",
        str(options.cout_estime_dialogue),
    ]
    if options.plafond_usd is not None:
        args += ["--plafond-usd", str(options.plafond_usd)]
    if options.graine is not None:
        args += ["--graine", str(options.graine)]
    if options.reprendre:
        args.append("--reprendre")
    args += options.personas
    with verrou_lanceur(DOSSIER_RUNS / campagne / FICHIER_VERROU), ExitStack() as pile:
        return lancer(args, campagne, pile)


def lancer(args: list[str], campagne: str, pile: ExitStack) -> int:
    """Les dialogues de la campagne, sous le verrou du lanceur."""
    exiger_simulation()
    from banc.dialogues import simulation

    pile.enter_context(simulation.reseau_interdit())
    nb_tours = int(lire_option(args, "--tours", "5"))
    intervalle = lire_option(args, "--intervalle-noa", None)
    if intervalle is not None:
        definir_intervalle_noa(float(intervalle))
    # Robustesse : reprise sans refaire les dialogues payes, plafond de depense de Noa,
    # isolement des erreurs par dialogue.
    reprendre = "--reprendre" in args
    if reprendre:
        args.remove("--reprendre")
    plafond_txt = lire_option(args, "--plafond-usd", None)
    plafond = float(plafond_txt) if plafond_txt is not None else None
    graine_txt = lire_option(args, "--graine", None)
    definir_graine(int(graine_txt) if graine_txt is not None else None)
    version_persona = choisir_version_persona(campagne, lire_option(args, "--version-persona", None))
    version_noa = lire_option(args, "--version-noa", None)
    choix_bras = lire_option(args, "--bras", "les_deux")
    bras_list = list(BRAS) if choix_bras == "les_deux" else [choix_bras]
    if any(b not in BRAS for b in bras_list):
        sys.exit(f"[X] --bras : {BRAS} ou les_deux")
    paralleles = int(lire_option(args, "--paralleles", "1"))
    if not 1 <= paralleles <= PARALLELES_MAX:
        sys.exit(f"[X] --paralleles : de 1 a {PARALLELES_MAX}")
    estime_txt = lire_option(args, "--cout-estime-dialogue", None)
    cout_estime = float(estime_txt) if estime_txt is not None else None
    source_simulation = lire_option(args, "--simulation", None)
    echelle_delais = float(lire_option(args, "--echelle-delais", "1"))
    refus = campagnes.refus_lancement(campagne, "simulation", DOSSIER_RUNS)
    if refus:
        sys.exit(f"[X] {refus}. Rien n'est lancé.")
    if source_simulation is None:
        sys.exit("[X] simulation obligatoire ; aucun autre mode disponible")
    source = source_autorisee(Path(source_simulation))
    versions_source = simulation.installer(sys.modules[__name__], source, echelle_delais)
    pile.callback(_restaurer_simulation)
    if version_persona not in versions_source:
        sys.exit(f"[X] version persona de la source : {sorted(versions_source)}")
    voulus = set(args)
    inconnus = voulus - set(PERSONAS)
    if inconnus:
        sys.exit(f"[X] personas inconnus : {sorted(inconnus)}")

    print("=" * 72)
    print(
        f"DIALOGUES — campagne {campagne} — {nb_tours} tours — bras {', '.join(bras_list)} — persona {MODELE_PERSONA} "
        f"(regles {version_persona})"
        + (f" — version de Noa {version_noa}" if version_noa else "")
        + f" — {paralleles} en parallele"
        + (f" — SIMULATION de {source_simulation}" if source_simulation else "")
    )
    print("  cible : doubles enregistrés locaux ; réseau et sous-processus interdits")
    print("=" * 72)

    fiches = [f for f in lire_toutes() if not voulus or f.identifiant in voulus]
    if not fiches:
        sys.exit("[X] aucune fiche synthétique sélectionnée dans personas/")
    horodatage = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    deja = runs_existants(campagne) if reprendre else {}
    a_faire = [(bras, f) for bras in bras_list for f in fiches if (f.identifiant, bras, version_noa) not in deja]
    if reprendre:
        print(f"  reprise : {len(deja)} dialogue(s) deja ecrit(s), non refait(s) ; {len(a_faire)} a faire")
    depense, n = depense_campagne(campagne)
    if plafond is not None:
        print(f"  plafond Noa : {plafond} $ ; deja depense dans la campagne : {depense} $ ({n} run(s))")
        if paralleles > 1 and n == 0 and not cout_estime:
            sys.exit(
                "[X] plafond en parallele sur une campagne sans dialogue mesure : --cout-estime-dialogue USD > 0 "
                "requis (chaque dialogue reserve son cout estime avant de demarrer). Rien n'est lance."
            )
    if paralleles == 1 and cout_estime is not None:
        print("  --cout-estime-dialogue ignoree en sequentiel (le plafond y projette la depense au cout moyen mesure)")
    # En parallele, plafond par reservation (parallele.Plafond) : cout estime reserve avant le demarrage, remplace
    # par le cout reel a la fin ; un dialogue en erreur garde sa reservation comme depense. En sequentiel : autoriser_sequentiel.
    budget = (
        Plafond(
            plafond,
            Counter({"noa": len(a_faire)}),
            deja={"noa": (depense, n)},
            estimations={"noa": cout_estime} if cout_estime is not None else None,
        )
        if paralleles > 1
        else None
    )
    reserves: dict[int, float] = {}
    runs: dict[int, dict] = {}
    echecs: list[str] = []
    definir_campagne_direct(campagne)

    def envoyer_cout() -> None:
        """En direct : depense Noa de la campagne comptee par le plafond (simulee en simulation), le plafond et la part
        du plafond consommee (celle de l'alerte a 80 %)."""
        depense_faite = budget.depense if budget is not None else depense_campagne(campagne)[0]
        envoyer_direct("cout_usd", depense_faite, etape="dialogues")
        if plafond is not None:
            envoyer_direct("plafond_usd", plafond, etape="dialogues")
            envoyer_direct("part_plafond", round(depense_faite / plafond, 6) if plafond > 0 else 1, etape="dialogues")

    def autoriser_sequentiel(rang: int) -> str | None:
        """Regle du plafond en sequentiel : depense relue dans les runs ecrits avant chaque dialogue,
        projection = depense + cout moyen x dialogues restants."""
        if plafond is None:
            return None
        depense, n = depense_campagne(campagne)
        moyenne = depense / n if n else 0.0
        projection = depense + moyenne * (len(a_faire) - rang)
        if depense >= plafond or projection > plafond:
            motif = (
                f"plafond {plafond} $ : depense {depense:.6f} $, projection {projection:.6f} $ "
                f"pour {len(a_faire) - rang} dialogue(s) restant(s). Rien d'autre n'est lance."
            )
            print(f"\n[ARRET] {motif}")
            return motif
        return None

    def autoriser(rang: int) -> str | None:
        if paralleles == 1:
            return autoriser_sequentiel(rang)
        montant, motif = budget.reserver("noa")
        if motif:
            print(f"\n[ARRET] {motif}")
            return motif
        bras, f = a_faire[rang]
        ecrire_reservation(campagne, horodatage, f.identifiant, bras, montant)
        reserves[rang] = montant
        return None

    def faire(tache: tuple[str, FicheBanc]) -> dict:
        bras, f = tache
        return dialoguer(
            f,
            horodatage,
            nb_tours,
            campagne,
            bras,
            version_persona=version_persona,
            version_noa=version_noa,
            paralleles=paralleles,
        )

    def terminer(rang: int, run: dict | None, erreur: Exception | None) -> None:
        bras, f = a_faire[rang]
        if erreur is None:
            if paralleles > 1:
                reserve, cout = reserves.pop(rang), cout_noa(run)
                if (run.get("cout", {}).get("analyse") or {}).get("file_oracle_analysis_queue") != "done":
                    # analyse n8n non terminee a l'ecriture du run : son cout arrivera apres ; la reservation reste
                    # comptee si elle est plus haute (n8n ne traite qu'un job par 30 s : plus frequent en parallele)
                    cout = max(cout, reserve)
                budget.liberer("noa", reserve, cout)  # la reservation a ete retiree par dialoguer()
            runs[rang] = run
            envoyer_direct("dialogues_termines")
            if run.get("statut_fin") == "TECHNICAL_ERROR":
                envoyer_direct("dialogues_tronques")
            envoyer_cout()
            return
        # une erreur n'arrete pas la campagne ; interruption et garde (production) traversent executer. En
        # parallele, la reservation reste comptee (ici, et par son fichier aux executions suivantes)
        if paralleles > 1:
            budget.liberer("noa", reserves.pop(rang), None)
        envoyer_direct("dialogues_tronques")
        envoyer_cout()
        message = f"{type(erreur).__name__} : {erreur}"
        print(f"\n  [X] {f.identifiant} · {bras} : {message[:300]}")
        noter_echec(campagne, f.identifiant, bras, message)
        echecs.append(f"{f.identifiant} · {bras}")
        runs[rang] = {
            "persona": {"identifiant": f.identifiant},
            "bras": bras,
            "resume": {"refus_persona": None, "tours_joues": 0, "duree_totale_s": 0},
            "cout": {"dialogue": None, "onboarding": None, "analyse": None},
            "compte": {"modeles_factures": []},
            "_fichier": None,
            "_erreur": message[:200],
        }

    debut = time.monotonic()
    envoyer_cout()
    _, arret = executer(
        a_faire, paralleles, faire, autoriser=autoriser, terminer=terminer, jauge=f"banc.en_cours.dialogues.{campagne}"
    )
    duree = time.monotonic() - debut
    if runs:  # rien n'a tourne : la duree d'une vraie execution n'est pas ecrasee
        envoyer_jauge(f"banc.duree_etape.dialogues.{campagne}.{paralleles}", round(duree, 1))

    print("\n" + "=" * 72 + "\nBILAN\n" + "=" * 72)
    print(
        f"  {'persona':<11} {'bras':<16} {'tours':>5} {'temps':>6} {'dialogue $':>11} {'onboard. $':>10} "
        f"{'analyse $':>9}  {'modèles facturés':<36} refus / erreur"
    )
    for r in (runs[rang] for rang in sorted(runs)):
        d, o, a = r["cout"]["dialogue"], r["cout"]["onboarding"], r["cout"]["analyse"]
        print(
            f"  {r['persona']['identifiant']:<11} {r['bras']:<16} {r['resume']['tours_joues']:>5} "
            f"{r['resume']['duree_totale_s']:>5.0f}s {(d['cout_usd'] if d else float('nan')):>11.6f} "
            f"{(o['cout_usd'] if o else float('nan')):>10.6f} {(a['cout_usd'] if a else float('nan')):>9.6f}  "
            f"{', '.join(m.replace('mistral-', '') for m in r['compte']['modeles_factures']) or '—':<36} "
            f"{r['resume'].get('refus_persona') or r.get('_erreur') or '—'}"
        )
    print(f"\n  {len(runs)} dialogue(s) en {duree:.0f} s, {paralleles} en parallele")
    print(f"  runs dans {DOSSIER_RUNS / campagne}")
    if plafond is not None:
        print(f"  depense Noa de la campagne : {depense_campagne(campagne)[0]} $ (plafond {plafond} $)")
    if source_simulation:
        bloquees = simulation.GARDE_RESEAU["bloquees"]
        print(
            f"  garde reseau de la simulation : {len(bloquees)} tentative(s) refusee(s)"
            + (f" ({', '.join(sorted(set(bloquees)))})" if bloquees else "")
        )
    if echecs:
        print(
            f"  [X] {len(echecs)} dialogue(s) en echec (journal runs/{campagne}/_echecs.json) : "
            f"{', '.join(echecs)} ; relancer avec --reprendre pour les retenter"
        )
        return 2
    if arret:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

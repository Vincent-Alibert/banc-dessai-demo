"""Doubles enregistrés du vrai lanceur, sans réseau ni sous-processus.
Le jeu synthétique C01 alimente inscription, onboarding, tours et file d'analyse en mémoire.
Les coûts payés sont nuls ; cout.simule_usd conserve le scénario historique pour le budget.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator

from banc.campagnes import campagnes
from banc.personas import reponses as moteur_persona
from banc.dialogues import contrats as etat_simulation
from banc.personas.fiche_banc import FicheBanc
from banc.personas.reponses import Reponse
from banc.campagnes.parallele import attendre_interruptible, verifier_interruption
from banc.garde import source_autorisee, nombre_positif, PERSONAS
import hashlib

# Mesures de C03 a C15 (76 dialogues dont la fin est datee ; docs/exploitation/DEMO.md, section 10), en secondes
PREPARATION_S = 0.7            # mediane (0,65 s) : inscription, cookie, contexte, quetes
ONBOARDING_S = 8.7             # MOYENNE des 30 dialogues avec onboarding dates (mediane 8,65 s)
METROLOGIE_S = 0.0             # mediane et p90 : la metrologie est deja ecrite a la fin du dernier tour
APRES_ANALYSE_S = 2.4          # mediane (2,43 s) : dernier sondage, lecture des couts, ecriture du run
# n8n_banc (n8n/post-analysis.local.json) : toutes les 30 s, claim_next_analysis_job() prend UNE ligne en attente de
# oracle_analysis_queue, la plus ancienne (created_at) ; la route remet la ligne de la conversation en attente a
# chaque tour. dialoguer.attendre_analyse attend au plus 75 s.
CYCLE_N8N_S = 30.0
TRAITEMENT_N8N_S = 5.0         # appel Mistral et ecriture (post_analysis apparait 1 a 10 s apres la prise)
DELAI_ANALYSE_S = 75.0

_FIL = threading.local()       # run source du dialogue en cours, propre a chaque worker

# Garde reseau de la simulation : evenements d'audit refuses pendant reseau_interdit()
EVENEMENTS_INTERDITS = (
    "socket.__new__", "socket.connect", "socket.connect_ex", "socket.bind", "socket.getaddrinfo",
    "socket.gethostbyname", "socket.sendto", "socket.sendmsg", "urllib.Request", "http.client.connect",
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile",
    "os.fork", "os.forkpty", "pty.spawn",
)
GARDE_RESEAU: dict = {"crochet": False, "active": False, "bloquees": []}


def _crochet(evenement: str, args: tuple) -> None:
    socket_reseau = evenement.startswith("socket.") and evenement != "socket.gethostname"
    if GARDE_RESEAU["active"] and (socket_reseau or evenement in EVENEMENTS_INTERDITS):
        GARDE_RESEAU["bloquees"].append(evenement)
        raise PermissionError(f"simulation : {evenement} refusé (ni réseau ni sous-processus)")


@contextmanager
def reseau_interdit() -> Iterator[None]:
    """Crochet d'audit actif sur tous les workers ; aucune exception UDP ou métrique.
    Les accès Postgres, dbt et Metabase appartiennent aux tâches suivantes, hors de ce processus.
    """
    if not GARDE_RESEAU["crochet"]:
        sys.addaudithook(_crochet)
        GARDE_RESEAU["crochet"] = True
    precedente = GARDE_RESEAU["active"]
    if not precedente:
        GARDE_RESEAU["bloquees"] = []
    GARDE_RESEAU["active"] = True
    try:
        yield
    finally:
        GARDE_RESEAU["active"] = precedente


class N8nSimule:
    """La file oracle_analysis_queue et n8n_banc, simules et PARTAGES par les workers : une ligne par conversation,
    remise en attente a chaque tour ; a chaque cycle, n8n traite une seule ligne, la plus ancienne en attente.
    Avec plusieurs dialogues en cours, l'analyse d'un dialogue fini attend donc les autres."""

    def __init__(self, echelle: float) -> None:
        if echelle <= 0:
            raise ValueError("la file temporisée attend une échelle strictement positive")
        self.echelle = echelle
        self._arret = threading.Event()
        self.lignes: dict[str, dict] = {}
        self.condition = threading.Condition()
        self._fil = threading.Thread(target=self._cycles, name="analyse-simulee", daemon=True)
        self._fil.start()

    def fermer(self) -> None:
        self._arret.set()
        with self.condition:
            self.condition.notify_all()
        self._fil.join(timeout=2)


    def mettre_en_attente(self, conversation: str) -> None:
        with self.condition:
            ligne = self.lignes.setdefault(conversation, {"creee": time.monotonic(), "version": 0, "traitee": 0})
            ligne["version"] += 1

    def _cycles(self) -> None:
        while not self._arret.wait(CYCLE_N8N_S * self.echelle):
            with self.condition:
                en_attente = [(l["creee"], c) for c, l in self.lignes.items() if l["traitee"] < l["version"]]
                if not en_attente:
                    continue
                ligne = self.lignes[min(en_attente)[1]]
                version = ligne["version"]
            if self._arret.wait(TRAITEMENT_N8N_S * self.echelle):
                return
            with self.condition:
                ligne["traitee"] = max(ligne["traitee"], version)
                self.condition.notify_all()

    def attendre(self, conversation: str | None) -> str:
        """Comme dialoguer.attendre_analyse : « done » si la derniere mise en attente est traitee dans les 75 s."""
        if not conversation or conversation not in self.lignes:
            return "absente"
        fin = time.monotonic() + DELAI_ANALYSE_S * self.echelle
        with self.condition:
            ligne = self.lignes[conversation]
            while ligne["traitee"] < ligne["version"]:
                reste = fin - time.monotonic()
                if reste <= 0:
                    return "pending"
                self.condition.wait(reste)
            return "done"


def installer(module: ModuleType, dossier: Path, echelle: float) -> set[str]:
    """Remplace dans `module` (dialoguer) les appels vers l'exterieur par le rejeu des runs de `dossier`.
    Renvoie les versions du persona des runs source (la campagne doit etre dans l'une d'elles). Source scellee
    (juges/SCELLE) : refus avant toute lecture d'un run."""
    dossier = source_autorisee(dossier)
    nombre_positif(str(echelle))
    if not GARDE_RESEAU["active"]:
        raise PermissionError("installer : la garde réseau doit précéder l'installation des doubles")
    refus = campagnes.refus_source_scellee(dossier)
    if refus:
        sys.exit(f"[X] --simulation : {refus}")
    empreintes = json.loads((dossier / "empreintes.json").read_text(encoding="utf-8"))
    fichiers = campagnes.fichiers_de_runs(dossier)
    if set(empreintes) != {f.name for f in fichiers}:
        raise ValueError("jeu synthétique incomplet ou fichiers inattendus")
    sources: dict[tuple[str, str], tuple[str, dict]] = {}
    for f in fichiers:
        octets = f.read_bytes()
        if hashlib.sha256(octets).hexdigest() != empreintes[f.name]:
            raise ValueError("empreinte du jeu synthétique invalide : " + f.name)
        run = json.loads(octets)
        ident, bras = run["persona"]["identifiant"], run["bras"]
        if ident not in PERSONAS or bras not in ("sans_onboarding", "avec_onboarding"):
            raise ValueError("couple persona/bras inconnu dans le jeu synthétique")
        if (ident, bras) in sources or run.get("provenance", {}).get("nature") != "jeu_synthetique":
            raise ValueError("jeu synthétique invalide ou doublon")
        if not 1 <= len(run["tours"]) <= 5 or [t["n"] for t in run["tours"]] != list(range(1, len(run["tours"]) + 1)):
            raise ValueError("séquence des tours invalide dans " + f.name)
        sources[(ident, bras)] = (f.name, run)
    if len(sources) != len(PERSONAS) * 2:
        raise ValueError("les cinq personas et leurs deux bras sont requis")
    n8n = N8nSimule(echelle) if echelle > 0 else None

    def dormir(secondes: float) -> None:
        if secondes * echelle > 0:
            attendre_interruptible(secondes * echelle)

    def tour(n: int) -> dict | None:
        tours = _FIL.source["tours"]
        return tours[n - 1] if n <= len(tours) else None

    def inscrire(email: str, mot_de_passe: str, username: str, first_name: str) -> dict:
        verifier_interruption()
        ident, court = email.split(".")[:2]
        bras = "avec_onboarding" if court == "avec" else "sans_onboarding"
        if (ident, bras) not in sources:
            raise RuntimeError(f"simulation : aucun run source pour {ident} · {bras} dans {dossier}")
        _FIL.nom, _FIL.source = sources[(ident, bras)]
        _FIL.ident, _FIL.bras = ident, bras
        dormir(PREPARATION_S)
        return {"user": {"id": f"simule-{ident}-{bras}"}, "access_token": "simule"}

    def onboarder(fiche: FicheBanc, porte: etat_simulation.PorteCookies) -> dict:
        verifier_interruption()
        dormir(ONBOARDING_S)
        return {"corps_envoye": None, "reponse": {"status": "simule"}, "duree_s": ONBOARDING_S}

    def premier_message(texte: str, fiche: FicheBanc, prenom: str | None = None) -> tuple[str, bool]:
        return tour(1)["persona"], False

    def jouer_un_tour(messages: list[dict], porte: etat_simulation.PorteCookies, n: int, contexte: dict | None = None,
                      extra: dict | None = None) -> tuple[str, float, dict]:
        etat_simulation.espacer_appel_noa()
        verifier_interruption()
        t = tour(n)
        if t is None or t.get("noa") is None:
            raise etat_simulation.RefusStrict((t or {}).get("erreur_oracle") or f"simulation : aucun tour {n} dans la source")
        dormir(t.get("noa_duree_s") or 0.0)
        etat_simulation.CONVERSATION["id"] = f"simule-conv-{_FIL.ident}-{_FIL.bras}"     # propre au worker
        if n8n:
            n8n.mettre_en_attente(etat_simulation.CONVERSATION["id"])      # la route met l'analyse en file a chaque tour
        return (t.get("noa_brut") or t["noa"], t.get("noa_duree_s") or 0.0,
                {"X-Oracle-Model": t.get("noa_modele_entete"), "X-Oracle-Tier": t.get("noa_tier"),
                 "X-Oracle-Remaining": t.get("mana_restante"), "X-Oracle-Safety": t.get("safety")})

    def repondre(fiche: FicheBanc, historique: list[dict], temperature: float = 0.8, version: str = "v1") -> Reponse:
        n = len(historique) // 2 + 1
        attente = moteur_persona.LIMITE_NVIDIA.attendre()     # la vraie file commune, sans appel
        verifier_interruption()
        depart = _FIL.source.get("depart")
        if depart and depart.get("apres_tour") == n - 1:
            dormir(depart.get("persona_duree_s") or 0.0)
            r = Reponse("", depart.get("persona_duree_s") or 0.0, None, None, "stop", attente_debit_s=attente)
            r.decision, r.motif, r.decision_forme = depart["statut"], depart.get("motif"), depart.get("decision_forme")
            r.depart_categorie = depart.get("categorie")
            return r
        t = tour(n)
        dormir((t or {}).get("persona_duree_s") or 0.0)
        if t is None or t.get("persona") is None:
            return Reponse("", (t or {}).get("persona_duree_s") or 0.0, None, None, None, attente_debit_s=attente,
                           refus=(t or {}).get("refus_persona") or "simulation : aucun message enregistre")
        r = Reponse(t["persona"], t.get("persona_duree_s") or 0.0, None, None, "stop", attente_debit_s=attente)
        r.decision, r.decision_forme = t.get("persona_decision"), t.get("persona_decision_forme")
        return r

    def attendre_metrologie(user_id: str, tours_joues: int, delai_max_s: float = 45) -> int:
        dormir(METROLOGIE_S)
        return tours_joues

    def attendre_analyse(conversation_id: str | None, delai_max_s: float = 75) -> str:
        _FIL.statut_analyse = n8n.attendre(conversation_id) if n8n else "done"
        return _FIL.statut_analyse

    def couts(conversation_id: str | None, user_id: str) -> dict:
        dormir(APRES_ANALYSE_S)
        source = _FIL.source.get("cout") or {}
        zero = {"appels": 0, "tokens": 0, "cout_usd": 0}
        return {"dialogue": dict(zero), "onboarding": dict(zero),
                "analyse": {**zero, "file_oracle_analysis_queue": _FIL.statut_analyse,
                            "note": "simulation : aucun workflow externe ; file d’analyse en mémoire"},
                "detail": [], "non_metres_connus": [],
                "simule_usd": round(sum((source.get(k) or {}).get("cout_usd") or 0
                                        for k in ("dialogue", "onboarding", "analyse")), 6),
                "note": (f"simulation du jeu C01/{_FIL.nom} : aucun appel, aucune dépense ; simule_usd "
                         "= cout Noa mesure de la source, pour le plafond")}

    def enrichir(run):
        source = _FIL.source
        run["constats"] = list(source.get("constats") or [])
        if source.get("marquage"):
            run["marquage"] = source["marquage"]
        run["provenance"] = {
            "mode": "simulation", "jeu": "C01", "fichier_source": _FIL.nom,
            "sha256_source": empreintes[_FIL.nom],
            "description": "vrai lanceur avec doubles enregistrés ; aucun appel applicatif ou modèle",
        }
        run["modele_persona"].update({"raisonnement": "enregistré", "penalites": "non applicables",
                                       "regles_du_banc": "messages synthétiques enregistrés, aucune génération"})
        run["cout"]["note"] = ("Dépenses de cette exécution : 0 $. Coût simulé : scénario complet de la source, "
                                  "non proratisé si moins de tours ; réservé séparément par le lanceur.")

    toutes = module.lire_toutes
    remplacements = {
        # les fiches des seuls personas de la source (les autres n'ont rien a rejouer)
        "lire_toutes": lambda: [f for f in toutes() if any(f.identifiant == i for i, _ in sources)],
        "_enrichir_simulation": enrichir,
        "inscrire": inscrire, "forger_cookies": lambda session: [], "onboarder": onboarder,
        "recuperer_contexte_utilisateur": lambda porte: _FIL.source.get("contexte_utilisateur") or {},
        "quetes_actives": lambda user_id, access_token: [], "bouton_accueil": lambda porte: tour(1)["persona"],
        "normaliser": premier_message, "jouer_un_tour": jouer_un_tour, "repondre": repondre,
        "fermer_session": lambda porte, conversation_id: 200, "attendre_metrologie": attendre_metrologie,
        "attendre_analyse": attendre_analyse, "couts": couts,
        "modeles_factures": lambda user_id: [t.get("noa_modele_facture") for t in _FIL.source["tours"] if t.get("noa")],
        "commit_application": lambda: dict((_FIL.source.get("cible") or {}).get("application") or {}),
        "modeles_non_metres": lambda: {}, "base_ciblee": lambda: "simulation", "BRAS_BANC": "simulation",
    }
    precedents = {nom: getattr(module, nom) for nom in remplacements}
    for nom, valeur in remplacements.items():
        if not hasattr(module, nom):
            raise AttributeError(f"simulation : {module.__name__}.{nom} introuvable")
        setattr(module, nom, valeur)
    def restaurer():
        if n8n:
            n8n.fermer()
        for nom, valeur in precedents.items():
            setattr(module, nom, valeur)
    module._restaurer_simulation = restaurer
    return {run.get("version_persona") or "v1" for _, run in sources.values()}

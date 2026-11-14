"""
Parallelisme du banc : dialogues et juges sur N workers (1 a PARALLELES_MAX), avec des
limites PARTAGEES entre les workers, jamais une par worker.

  executer        lance les taches dans l'ordre de la liste, au plus N a la fois ; avant chaque demarrage,
                  le plafond est consulte (reservation) ; a la fin de chaque tache, le cout reel remplace la
                  reservation. Les etapes d'une meme tache (les tours d'un dialogue) restent sequentielles :
                  une tache tourne entierement dans son worker.
  LimiteDebit     au plus N appels par fenetre glissante de P secondes, en UNE file commune : chaque appel
                  recoit son creneau dans l'ordre d'arrivee (onboarding de Noa 5 par minute, NVIDIA 40 par minute).
  Pause429        attente partagee apres un 429 : quand un worker prend un 429 de Mistral, tous les workers
                  attendent avant leur prochain appel a Noa ; attente doublee a chaque 429 consecutif, plafonnee.
  Plafond         plafond de depense par reservation : un dialogue (ou une notation) reserve son cout estime
                  avant de demarrer et le libere a la fin, remplace par son cout reel. Rien ne demarre si la
                  depense faite, plus les reservations en cours, plus l'estimation des taches non demarrees,
                  depasse le plafond (la projection des lanceurs sequentiels, etendue aux taches en vol).
                  Reservations et liberations se font dans le seul fil principal (executer : autoriser,
                  terminer), l'une apres l'autre : c'est ce qui rend la regle exacte, sans verrou.
  INTERRUPTION    drapeau d'arret : Ctrl+C, SIGTERM (arret d'une tache par Airflow ; executer installe le
                  gestionnaire) ou une erreur du fil principal le levent ; chaque worker le verifie avant chaque
                  appel payant (verifier_interruption) : une tache qui avait encore un appel payant a faire
                  s'arrete apres son appel en cours ; une tache qui n'en avait plus (fin d'un dialogue : fermeture,
                  metrologie, attente de l'analyse) va au bout. SIGKILL : rien ne tourne plus.
                  Les attentes (limites, pause 429, intervalle Noa) s'arretent des que le drapeau est leve
                  (attendre_interruptible) ; l'appel qui suit verifie le drapeau.
  verrou_lanceur  un seul lanceur par campagne et par etape (fichier runs/<C>/_lanceur-<etape>.lock, PID et machine).
  SortieParWorker chaque ligne du journal porte son worker ([w1], [w2]...).
  envoyer_jauge   interface conservée mais sans émetteur réseau ; aucune pile de métriques à démarrer.
  envoyer_direct  metrique « en direct » : appels, latence, erreurs, reprises, cout cumule
                  et plafond, dialogues termines ou tronques, notations faites (METRIQUES_DIRECT), pour la
                  campagne fixee par definir_campagne_direct. Seules etiquettes possibles : campagne, fournisseur,
                  etape ; jamais un persona, un dialogue ni une note de juge.
Aucun appel a un modele ici : ce module ne fait qu'ordonner, compter et attendre.
"""
from __future__ import annotations

import io
import json
import os
import re
import signal
import socket
import sys
import threading
import time
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, TextIO

from banc.campagnes import campagnes

PARALLELES_MAX = 4
BATTEMENT_JAUGE_S = 15.0       # la jauge des taches en cours est renvoyee au moins toutes les 15 s
INTERRUPTION = threading.Event()


class Interruption(Exception):
    """Levee dans un worker quand le lanceur s'arrete : aucun appel payant ne part plus."""


def _sigterm_en_interruption(signum: int, cadre: object) -> None:
    raise KeyboardInterrupt("SIGTERM")


def verifier_interruption() -> None:
    """A appeler juste avant chaque appel payant d'un worker, APRES toute attente (tour de Noa, persona,
    onboarding, essai d'un juge). La fermeture de session n'est pas verifiee, par conception : elle ne fait
    que mettre a jour la conversation (l'analyse n8n est declenchee par chaque tour de Noa)."""
    if INTERRUPTION.is_set():
        raise Interruption("lanceur interrompu : appel payant non lance")


def attendre_interruptible(secondes: float) -> None:
    """Attente qui s'arrete des que le lanceur est interrompu (l'appel qui suit verifie l'interruption)."""
    if secondes > 0:
        INTERRUPTION.wait(secondes)


@contextmanager
def verrou_lanceur(fichier: Path) -> Iterator[None]:
    """Un seul lanceur a la fois par campagne et par etape (runs/<C>/_lanceur-dialogues.lock, -juges.lock) : une
    relance (--reprendre, nouvel essai de la tache Airflow) pendant que l'ancien processus tourne encore referait
    des dialogues en cours. Le fichier porte le PID, la machine et l'heure ; il est retire a la sortie du lanceur
    (normale, erreur ou Ctrl+C). Verrou d'un PID mort sur la meme machine : perime, remplace. Verrou d'une autre
    machine (conteneur et poste ne voient pas les memes PID) ou illisible : refus, a retirer a la main apres
    avoir verifie qu'aucun lanceur ne tourne."""
    fichier.parent.mkdir(parents=True, exist_ok=True)
    contenu = json.dumps({"pid": os.getpid(), "machine": socket.gethostname(),
                          "depuis_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    for _ in range(2):
        try:
            with open(fichier, "x", encoding="utf-8") as f:       # creation exclusive : deux lanceurs, un gagnant
                f.write(contenu)
            break
        except FileExistsError:
            try:
                autre = json.loads(fichier.read_text(encoding="utf-8"))
                pid, machine = int(autre["pid"]), str(autre["machine"])
            except (OSError, ValueError, KeyError, TypeError):
                sys.exit(f"[X] verrou {fichier} illisible : verifier qu'aucun lanceur ne tourne, le retirer, relancer.")
            if machine != socket.gethostname() or processus_vivant(pid):
                sys.exit(f"[X] un lanceur tourne deja sur cette campagne (PID {pid}, machine {machine}, depuis "
                         f"{autre.get('depuis_utc')}) : rien n'est lance. S'il est arrete, retirer {fichier}.")
            print(f"  verrou perime (PID {pid} arrete) : remplace")
            fichier.unlink(missing_ok=True)
    else:
        sys.exit(f"[X] verrou {fichier} : pris par un autre lanceur au meme instant, rien n'est lance.")
    try:
        yield
    finally:
        fichier.unlink(missing_ok=True)


def processus_vivant(pid: int) -> bool:
    """Le processus `pid` tourne-t-il sur cette machine ? (Windows : OpenProcess ; jamais os.kill, qui y tue.)"""
    if os.name == "nt":
        import ctypes
        noyau = ctypes.windll.kernel32
        poignee = noyau.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not poignee:
            return False
        code = ctypes.c_ulong()
        noyau.GetExitCodeProcess(poignee, ctypes.byref(code))
        noyau.CloseHandle(poignee)
        return code.value == 259                                    # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class LimiteDebit:
    """Au plus `nombre` appels par fenetre glissante de `periode_s` secondes, partagee par tous les fils.

    Une seule file : chaque appel recoit, sous verrou et dans l'ordre d'arrivee, le premier creneau libre
    (au moins `periode_s` apres le creneau accorde `nombre` rangs plus tot), puis attend ce creneau hors du
    verrou. Deux creneaux sur `nombre` + 1 consecutifs sont donc toujours separes d'au moins `periode_s`."""

    def __init__(self, nombre: int, periode_s: float, horloge: Callable[[], float] = time.monotonic,
                 dormir: Callable[[float], None] = attendre_interruptible) -> None:
        if nombre < 1 or periode_s <= 0:
            raise ValueError("limite de debit : nombre >= 1 et periode > 0")
        self.nombre, self.periode_s = nombre, periode_s
        self._horloge, self._dormir = horloge, dormir
        self._creneaux: deque[float] = deque()
        self._verrou = threading.Lock()

    def attendre(self) -> float:
        """Attend le creneau de cet appel ; renvoie l'attente en secondes (0 si aucune)."""
        with self._verrou:
            maintenant = self._horloge()
            while self._creneaux and self._creneaux[0] <= maintenant - self.periode_s:
                self._creneaux.popleft()
            instant = maintenant
            if len(self._creneaux) >= self.nombre:
                instant = max(instant, self._creneaux[-self.nombre] + self.periode_s)
            if self._creneaux:
                instant = max(instant, self._creneaux[-1])       # ordre d'arrivee : jamais avant le precedent
            self._creneaux.append(instant)
        attente = instant - maintenant
        if attente > 0:
            self._dormir(attente)
        return round(max(attente, 0.0), 3)


class Pause429:
    """Attente partagee apres un 429 : `signaler` repousse la reprise de tous les workers de base_s, 2 x base_s,
    4 x base_s... (429 consecutifs), au plus plafond_s ; `succes` remet le palier a zero."""

    def __init__(self, base_s: float = 10.0, plafond_s: float = 60.0,
                 horloge: Callable[[], float] = time.monotonic,
                 dormir: Callable[[float], None] = attendre_interruptible) -> None:
        self.base_s, self.plafond_s = base_s, plafond_s
        self._horloge, self._dormir = horloge, dormir
        self._reprise = 0.0
        self._palier = 0
        self._verrou = threading.Lock()

    def signaler(self) -> float:
        """Un 429 vient d'arriver : renvoie l'attente avant la reprise (la meme pour tous les workers)."""
        with self._verrou:
            self._palier += 1
            maintenant = self._horloge()
            attente = min(self.plafond_s, self.base_s * 2 ** (self._palier - 1))
            self._reprise = max(self._reprise, maintenant + attente)
            return round(self._reprise - maintenant, 3)

    def attendre(self) -> float:
        """Avant un appel : attend la fin de la pause en cours, s'il y en a une ; renvoie l'attente."""
        with self._verrou:
            reste = self._reprise - self._horloge()
        if reste > 0:
            self._dormir(reste)
        return round(max(reste, 0.0), 3)

    def succes(self) -> None:
        with self._verrou:
            self._palier = 0


class Plafond:
    """Plafond de depense par reservation, par poste (« noa », ou un juge). Appele seulement depuis le fil
    principal d'executer (autoriser, terminer) : reservations et liberations se suivent, sans concurrence.

    Estimation d'une tache d'un poste : le plus haut du cout moyen mesure des taches finies de ce poste (dont
    celles d'executions precedentes, passees dans `deja`) et de l'estimation fournie, qui sert de plancher.
    Garantie : si l'estimation fournie majore le cout reel de chaque tache, la depense ne depasse jamais le
    plafond, quel que soit le nombre de workers (chaque tache en vol a reserve au moins son cout). Sans
    estimation fournie et sans cout mesure, l'estimation vaut 0. Dans une execution, une tache en erreur est
    comptee au montant reserve ; d'une execution a l'autre, c'est au lanceur de le reporter (dialoguer.py :
    fichiers de reservation)."""

    def __init__(self, plafond_usd: float | None, a_faire: Counter[str],
                 deja: dict[str, tuple[float, int]] | None = None,
                 estimations: dict[str, float] | None = None) -> None:
        self.plafond_usd = plafond_usd
        self._restants: Counter[str] = Counter(a_faire)
        self._mesure = {cle: [somme, n] for cle, (somme, n) in (deja or {}).items()}
        self._estimations = dict(estimations or {})
        self.depense = sum(somme for somme, _ in self._mesure.values())
        self.reserve = 0.0

    def estimation(self, cle: str) -> float:
        somme, n = self._mesure.get(cle, (0.0, 0))
        return max(somme / n if n else 0.0, self._estimations.get(cle, 0.0))

    def reserver(self, cle: str) -> tuple[float | None, str | None]:
        """(montant reserve, None) si la tache peut demarrer ; (None, motif) sinon : rien ne demarre plus."""
        montant = self.estimation(cle)
        if self.plafond_usd is not None:
            # au millionieme de dollar, la precision des couts du banc : 16 x 0,05 ne depasse pas 0,80
            projection = round(self.depense + self.reserve
                               + sum(self.estimation(k) * n for k, n in self._restants.items()), 6)
            if round(self.depense, 6) >= self.plafond_usd or projection > self.plafond_usd:
                return None, (f"plafond {self.plafond_usd} $ : depense {self.depense:.6f} $, reservations en cours "
                              f"{self.reserve:.6f} $, projection {projection:.6f} $ pour "
                              f"{sum(self._restants.values())} tache(s) non demarree(s). Rien d'autre n'est lance.")
        self._restants[cle] -= 1
        self.reserve += montant
        return montant, None

    def liberer(self, cle: str, reserve: float, cout_reel: float | None) -> None:
        """Fin d'une tache : la reservation est remplacee par le cout reel (ou gardee si le cout est inconnu)."""
        self.reserve = max(0.0, self.reserve - reserve)
        if cout_reel is None:
            self.depense += reserve
            return
        self.depense += cout_reel
        mesure = self._mesure.setdefault(cle, [0.0, 0])
        mesure[0] += cout_reel
        mesure[1] += 1


def etiquette_worker() -> str | None:
    """« w1 », « w2 »... dans un worker de `executer`, None ailleurs."""
    nom = threading.current_thread().name
    if nom.startswith("w_") and nom[2:].isdigit():
        return f"w{int(nom[2:]) + 1}"
    return None


class SortieParWorker(io.TextIOBase):
    """Enveloppe de sys.stdout : chaque ligne ecrite depuis un worker est prefixee par « [wN] » et ecrite
    d'un bloc (les lignes de deux workers ne se melangent pas) ; le fil principal ecrit tel quel."""

    def __init__(self, sortie: TextIO) -> None:
        super().__init__()
        self._sortie = sortie
        self._tampons: dict[int, str] = {}
        self._verrou = threading.Lock()

    def write(self, texte: str) -> int:
        nom = etiquette_worker()
        with self._verrou:
            if nom is None:
                self._sortie.write(texte)
                return len(texte)
            fil = threading.get_ident()
            *lignes, reste = (self._tampons.pop(fil, "") + texte).split("\n")
            for ligne in lignes:
                self._sortie.write(f"[{nom}] {ligne}\n")
            if reste:
                self._tampons[fil] = reste
        return len(texte)

    def vider(self) -> None:
        """Ecrit les fins de ligne restees en attente (sans retour a la ligne final)."""
        with self._verrou:
            for reste in self._tampons.values():
                self._sortie.write(reste + "\n")
            self._tampons.clear()

    def flush(self) -> None:
        with self._verrou:
            self._sortie.flush()


_JAUGE_EN_ECHEC: set[str] = set()


def envoyer_jauge(nom: str, valeur: float, type_statsd: str = "g") -> None:
    """Compatibilité de l'ordonnanceur : émetteur réseau supprimé dans cette distribution."""
    return None


# Metriques en direct (rangee « En direct » de Grafana, infra/monitoring/statsd/mapping.yml) : mesure -> (type
# StatsD, etiquettes apres la campagne). Aucune autre etiquette : ni persona, ni dialogue, ni note de juge, meme agregee.
METRIQUES_DIRECT: dict[str, tuple[str, tuple[str, ...]]] = {
    "appels": ("c", ("fournisseur", "etape")),            # un appel vu du lanceur (essai compris)
    "latence": ("ms", ("fournisseur", "etape")),          # duree d'un appel reussi, passee en secondes
    "erreurs_429": ("c", ("fournisseur", "etape")),
    "erreurs_500": ("c", ("fournisseur", "etape")),
    "erreurs_504": ("c", ("fournisseur", "etape")),       # 504, ou delai depasse cote client
    "erreurs_autres": ("c", ("fournisseur", "etape")),
    "reprises": ("c", ("fournisseur", "etape")),          # nouvel essai apres une erreur (429 de Mistral, juge)
    "cout_usd": ("g", ("etape",)),                        # cumul de la campagne : Noa (dialogues), juges
    "plafond_usd": ("g", ("etape",)),                     # plafond du lanceur (juges : celui de l'execution)
    "part_plafond": ("g", ("etape",)),                    # depense comptee par ce plafond / plafond (alerte a 80 %)
    "dialogues_termines": ("c", ()),                      # run ecrit, quel que soit le statut de fin
    "dialogues_tronques": ("c", ()),                      # TECHNICAL_ERROR, ou erreur du lanceur sans run
    "notations": ("c", ("fournisseur",)),                 # notation valide ecrite
}
FOURNISSEURS_DIRECT = ("mistral", "nvidia", "google", "openai", "factice")
ETAPES_DIRECT = ("noa", "onboarding", "persona", "juge", "dialogues", "juges")
_CAMPAGNE_DIRECT: dict[str, str | None] = {"campagne": None}


def definir_campagne_direct(campagne: str | None) -> None:
    """Campagne des metriques en direct : un lanceur ne traite qu'une campagne (verrou_lanceur)."""
    if campagne is not None and not campagnes.code_valide(campagne):      # nomenclature : campagnes.py
        raise ValueError(f"campagne {campagne!r} : {campagnes.FORMATS}, ou un ancien nom (C00 a C15, C90 a C99)")
    _CAMPAGNE_DIRECT["campagne"] = campagne


def code_erreur(texte: str) -> str:
    """Mesure d'erreur d'un message d'erreur : erreurs_429, _500, _504 (delai depasse cote client compris), sinon
    _autres. Le code « HTTP nnn » du banc l'emporte (un corps de reponse peut contenir d'autres nombres)."""
    http = re.search(r"\bHTTP (\d{3})\b", texte)
    if http:
        code = http.group(1)
    elif re.search(r"Timeout|timed out|DEADLINE", texte):
        code = "504"
    else:
        nombre = re.search(r"\b(429|500|504)\b", texte)          # « Mistral API error 429 », erreurs des SDK
        code = nombre.group(1) if nombre else ""
    return f"erreurs_{code}" if code in ("429", "500", "504") else "erreurs_autres"


def envoyer_direct(mesure: str, valeur: float = 1, fournisseur: str | None = None, etape: str | None = None) -> None:
    """Metrique en direct de la campagne fixee (definir_campagne_direct) ; sans campagne, rien. Les etiquettes
    sont celles de METRIQUES_DIRECT, aux valeurs de FOURNISSEURS_DIRECT et ETAPES_DIRECT : toute autre valeur
    leve ValueError (une erreur de programmation, jamais un evenement d'une campagne)."""
    campagne = _CAMPAGNE_DIRECT["campagne"]
    type_statsd, etiquettes = METRIQUES_DIRECT[mesure]
    valeurs = {"fournisseur": fournisseur, "etape": etape}
    if (fournisseur is not None) != ("fournisseur" in etiquettes) or (etape is not None) != ("etape" in etiquettes):
        raise ValueError(f"metrique {mesure} : etiquettes attendues {etiquettes}")
    if (fournisseur is not None and fournisseur not in FOURNISSEURS_DIRECT) or (etape is not None and etape not in ETAPES_DIRECT):
        raise ValueError(f"metrique {mesure} : fournisseur {fournisseur!r} ou etape {etape!r} inconnu")
    if campagne is None:
        return
    if type_statsd == "ms":
        valeur = round(valeur * 1000, 1)         # duree passee en secondes, envoyee en millisecondes (StatsD)
    envoyer_jauge(".".join(["banc.direct", mesure, campagne, *(valeurs[e] for e in etiquettes)]), valeur, type_statsd)


def compter_appel(fournisseur: str, etape: str, duree_s: float | None = None, erreur: str | None = None) -> None:
    """Un appel vu du lanceur : compte, puis sa latence s'il a reussi, ou son erreur (code_erreur)."""
    envoyer_direct("appels", fournisseur=fournisseur, etape=etape)
    if erreur is not None:
        envoyer_direct(code_erreur(erreur), fournisseur=fournisseur, etape=etape)
    elif duree_s is not None:
        envoyer_direct("latence", duree_s, fournisseur=fournisseur, etape=etape)


def compter_exception(fournisseur: str, etape: str, erreur: Exception) -> None:
    """Exception levee par un appel (delai depasse, connexion refusee...) : un appel en erreur (code_erreur ; le delai
    depasse compte en 504, le reste en « autres »). L'interruption du lanceur n'est pas un appel : rien n'est compte."""
    if not isinstance(erreur, Interruption):
        compter_appel(fournisseur, etape, erreur=f"{type(erreur).__name__} : {erreur}")


def executer(taches: list[Any], paralleles: int, faire: Callable[[Any], Any], *,
             autoriser: Callable[[int], str | None] = lambda rang: None,
             terminer: Callable[[int, Any, Exception | None], None] = lambda rang, resultat, erreur: None,
             jauge: str | None = None) -> tuple[list[tuple[int, Any, Exception | None]], str | None]:
    """Lance faire(tache) pour chaque tache, dans l'ordre de la liste, au plus `paralleles` a la fois.

    Avant chaque demarrage, autoriser(rang) : None pour demarrer, sinon le motif d'arret : plus rien ne
    demarre, les taches en cours vont au bout. A la fin de chaque tache, terminer(rang, resultat, erreur)
    est appele dans le fil principal, avant tout nouveau demarrage. Une Exception d'une tache est rendue
    (elle n'arrete pas les autres) ; une interruption (KeyboardInterrupt, SystemExit) ou une erreur du fil
    principal leve INTERRUPTION (les workers ne lancent plus aucun appel payant et s'arretent a leur prochaine
    verification), puis est relancee une fois les workers arretes. Renvoie ([(rang, resultat, erreur)] dans l'ordre des rangs,
    motif d'arret ou None). La jauge `jauge` suit le nombre de taches en cours."""
    if not 1 <= paralleles <= PARALLELES_MAX:
        raise ValueError(f"paralleles : de 1 a {PARALLELES_MAX}, pas {paralleles}")
    sortie_d_origine = sys.stdout
    sortie = SortieParWorker(sortie_d_origine)
    sys.stdout = sortie
    resultats: dict[int, tuple[int, Any, Exception | None]] = {}
    en_vol: dict[Future, int] = {}
    arret: str | None = None
    suivant = 0
    pool = ThreadPoolExecutor(max_workers=paralleles, thread_name_prefix="w")
    INTERRUPTION.clear()
    # SIGTERM (arret d'une tache par Airflow) traite comme Ctrl+C : sans cela, le processus mourrait sans lever le
    # drapeau. Un signal n'est recu que par le fil principal, seul a pouvoir installer le gestionnaire.
    sigterm_precedent = (signal.signal(signal.SIGTERM, _sigterm_en_interruption)
                         if threading.current_thread() is threading.main_thread() else None)
    try:
        while True:
            while arret is None and suivant < len(taches) and len(en_vol) < paralleles:
                arret = autoriser(suivant)
                if arret is None:
                    en_vol[pool.submit(faire, taches[suivant])] = suivant
                    suivant += 1
            if jauge:
                envoyer_jauge(jauge, len(en_vol))
            if not en_vol:
                break
            finis, _ = wait(list(en_vol), timeout=BATTEMENT_JAUGE_S, return_when=FIRST_COMPLETED)
            for fut in sorted(finis, key=lambda f: en_vol[f]):
                rang = en_vol.pop(fut)
                erreur = fut.exception()
                if erreur is not None and not isinstance(erreur, Exception):
                    raise erreur                                 # interruption : rien n'est masque
                resultats[rang] = (rang, None if erreur else fut.result(), erreur)
                terminer(rang, resultats[rang][1], erreur)
    except BaseException:
        INTERRUPTION.set()          # plus aucun appel payant : l'appel en cours de chaque worker est le dernier
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        if sigterm_precedent is not None:
            signal.signal(signal.SIGTERM, sigterm_precedent)
        sortie.vider()
        sys.stdout = sortie_d_origine
        if jauge:
            envoyer_jauge(jauge, 0)
    return [resultats[r] for r in sorted(resultats)], arret

"""État local du lanceur et emplacements réservés aux doubles enregistrés.
Ce module ne connaît aucune URL applicative, aucun secret et aucun client réseau.
"""
from __future__ import annotations

import threading
import time

from banc.campagnes.parallele import attendre_interruptible, verifier_interruption


class _ConversationDuFil(threading.local):
    """Conversation en cours, PROPRE A CHAQUE FIL : avec des dialogues en parallele, chaque worker a son
    conversationId ; un dictionnaire commun melangerait les conversations de deux dialogues.
    S'utilise comme un dictionnaire : CONVERSATION["id"]."""

    def __init__(self) -> None:
        self.valeurs: dict[str, str | None] = {"id": None}

    def __getitem__(self, cle: str) -> str | None:
        return self.valeurs[cle]

    def __setitem__(self, cle: str, valeur: str | None) -> None:
        self.valeurs[cle] = valeur


CONVERSATION = _ConversationDuFil()
_INTERVALLE = 0.0
_DERNIER = 0.0
_VERROU_INTERVALLE = threading.Lock()


class RefusStrict(Exception):
    """Refus enregistré dans le jeu synthétique."""


class PorteCookies:
    """Conteneur vide conservant le contrat du lanceur, sans authentification."""
    def __init__(self, cookies):
        self.rotations = 0


def definir_intervalle_noa(secondes):
    global _INTERVALLE, _DERNIER
    _INTERVALLE, _DERNIER = secondes, 0.0


def espacer_appel_noa():
    global _DERNIER
    with _VERROU_INTERVALLE:
        attendre_interruptible(max(0.0, _DERNIER + _INTERVALLE - time.monotonic()))
        verifier_interruption()
        _DERNIER = time.monotonic()


def non_installe(*args, **kwargs):
    raise PermissionError("simulation non installée : opération externe absente de cette distribution")


inscrire = forger_cookies = onboarder = recuperer_contexte_utilisateur = non_installe
quetes_actives = bouton_accueil = jouer_un_tour = fermer_session = non_installe
attendre_metrologie = attendre_analyse = couts = modeles_factures = non_installe
commit_application = modeles_non_metres = non_installe


def base_ciblee():
    return "simulation"

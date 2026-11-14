"""Contrat local d'une réponse enregistrée ; aucun client ni moteur de génération."""

from __future__ import annotations

from dataclasses import dataclass, field

from banc.campagnes.parallele import LimiteDebit

MODELE = "persona-enregistre-v1"
VERSIONS_PERSONA = ("v1",)
GRAINE = None
LIMITE_NVIDIA = LimiteDebit(40, 60.0)


def definir_graine(graine: int | None) -> None:
    global GRAINE
    GRAINE = graine


@dataclass
class Reponse:
    texte: str
    duree_s: float
    tokens_entree: int | None
    tokens_sortie: int | None
    fin: str | None
    refus: str | None = None  # None = tout va bien
    raisonnement_present: bool = False
    coupe: bool = False
    derive_anglais: str | None = None  # None, "reprise" (2e appel propre) ou "coupee"
    tentatives_429: int = 0
    extra: dict = field(default_factory=dict)
    # persona v2 : decision de fin, prise apres la derniere reponse de Noa (None en v1)
    decision: str | None = None
    motif: str | None = None
    decision_forme: str | None = None  # stricte, toleree, relance (obtenue par la relance), repli
    decision_brute: str | None = None
    message_non_envoye: str | None = None  # texte ecrit avec un depart : garde dans le run, jamais envoye
    decision_relance: bool = False  # une relance de la seule decision a ete faite (voir relancer_decision)
    decision_brute_relance: str | None = None  # sortie brute de la relance, telle que le modele l'a ecrite
    # persona v2.2 : QUIT_NEGATIVE incoherent avec son message, traite comme CONTINUE (voir coherence_depart)
    depart_incoherent: bool = False
    depart_incoherent_raison: str | None = None
    depart_ecarte_motif: str | None = None  # motif du depart ecarte
    depart_ecarte_brute: str | None = None  # ligne de decision du depart ecarte, telle qu'ecrite
    # persona v2.2 : categorie d'un QUIT_NEGATIVE retenu : « coherent » (abandon) ou « ambigu » (dialogue
    # arrete, pas un abandon, a relire) ; None hors v2.2 ou sans QUIT_NEGATIVE
    depart_categorie: str | None = None
    depart_categorie_raison: str | None = None
    # attente de la limite NVIDIA partagee par les workers (40 par minute), hors duree_s
    attente_debit_s: float = 0.0


def normaliser(*args, **kwargs):
    raise PermissionError("simulation non installée : aucun moteur persona disponible")


def repondre(*args, **kwargs):
    raise PermissionError("simulation non installée : aucun moteur persona disponible")

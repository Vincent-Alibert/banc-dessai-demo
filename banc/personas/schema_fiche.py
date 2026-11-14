"""Schéma des fiches synthétiques : champs et énumérations repris du schéma source.
Validation stricte avec la bibliothèque standard, sans moteur de génération ni dépendance LLM.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from typing import get_args, get_origin, get_type_hints


class Donnees:
    def model_dump(self):
        return asdict(self)

    def model_dump_json(self, *, indent=None, exclude_none=False):
        return json.dumps(
            self.model_dump(), ensure_ascii=False, indent=indent, separators=None if indent else (",", ":")
        )

    @classmethod
    def model_validate_json(cls, texte):
        return cls.model_validate(json.loads(texte))

    @classmethod
    def model_validate(cls, valeurs):
        if not isinstance(valeurs, dict):
            raise ValueError(cls.__name__ + " : objet JSON attendu")
        noms = {f.name for f in fields(cls)}
        if set(valeurs) - noms:
            raise ValueError(cls.__name__ + " : champs inconnus " + str(sorted(set(valeurs) - noms)))
        types = get_type_hints(cls)
        resultat = {k: _valider(types[k], v, cls.__name__ + "." + k) for k, v in valeurs.items()}
        try:
            return cls(**resultat)
        except TypeError as exc:
            raise ValueError(cls.__name__ + " : champs obligatoires absents") from exc


def _valider(type_attendu, valeur, chemin):
    if get_origin(type_attendu) is list:
        if not isinstance(valeur, list):
            raise ValueError(chemin + " : liste attendue")
        return [_valider(get_args(type_attendu)[0], x, chemin) for x in valeur]
    if isinstance(type_attendu, type) and issubclass(type_attendu, Donnees):
        return type_attendu.model_validate(valeur)
    if isinstance(type_attendu, type) and issubclass(type_attendu, StrEnum):
        try:
            return type_attendu(valeur)
        except (ValueError, TypeError) as exc:
            raise ValueError(chemin + " : valeur hors énumération") from exc
    valide = type(valeur) is type_attendu
    if type_attendu is float:
        valide = type(valeur) in (int, float)
    if not valide:
        raise ValueError(chemin + " : type invalide")
    return valeur


class Position(StrEnum):
    """Position du persona dans le parcours de soin.

    Cinq stades : crise aiguë, plateau, progression, rechute, prévention.
    """

    CRISE_AIGUE = "crise_aigue"
    PLATEAU = "plateau"
    PROGRESSION = "progression"
    RECHUTE = "rechute"
    PREVENTION = "prevention"


class MotifEntree(StrEnum):
    """Motif qui a amené le persona à entrer en contact avec le chatbot.

    Cinq motifs : sur conseil, propre recherche, crise, retour, curiosité.
    """

    SUR_CONSEIL = "sur_conseil"
    PROPRE_RECHERCHE = "propre_recherche"
    CRISE = "crise"
    RETOUR = "retour"
    CURIOSITE = "curiosite"


class Resistance(StrEnum):
    """Résistance du persona à l'aide proposée.

    Trois niveaux : faible, moyenne, élevée.
    """

    FAIBLE = "faible"
    MOYENNE = "moyenne"
    ELEVE = "eleve"


class LitteratieIA(StrEnum):
    """Niveau de littératie IA du persona (connaissance des IA).

    Quatre niveaux : naïve, curieuse, compétente, experte.
    """

    NAIVE = "naive"
    CURIEUSE = "curieuse"
    COMPETENTE = "competente"
    EXPERTE = "experte"


class StyleEcriture(StrEnum):
    """Style d'écriture du persona dans ses messages.

    Quatre styles : télégraphique, courant, verbeux, émotionnel.
    """

    TELEGRAPHIQUE = "telegraphique"
    COURANT = "courant"
    VERBEUX = "verbeux"
    EMOTIONNEL = "emotionnel"


class Regularite(StrEnum):
    """Régularité de présence du persona dans les conversations.

    Quatre profils : quotidien, régulier, irrégulier, épisodique.
    """

    QUOTIDIEN = "quotidien"
    REGULIER = "regulier"
    IRREGULIER = "irregulier"
    EPISODIQUE = "episodique"


class DensiteEmotionnelle(StrEnum):
    """Densité émotionnelle des messages du persona.

    Quatre niveaux : froid, neutre, expressif, intense.
    """

    FROID = "froid"
    NEUTRE = "neutre"
    EXPRESSIF = "expressif"
    INTENSE = "intense"


class CasSensible(StrEnum):
    """Cas sensible (ou absence) intégré à la situation du persona.

    Douze cas : aucun, idéation suicidaire, auto-mutilation, TCA, dépendance,
    maltraitance, psychotique, deuil, crise aiguë, cyberharcèlement, maladie
    grave, risque enfant.
    """

    AUCUN = "aucun"
    IDEATION_SUICIDAIRE = "ideation_suicidaire"
    AUTO_MUTILATION = "auto_mutilation"
    TCA = "tca"
    DEPENDANCE = "dependance"
    MALTRAITANCE = "maltraitance"
    PSYCHOTIQUE = "psychotique"
    DEUIL = "deuil"
    CRISE_AIGUE = "crise_aigue"
    CYBERHARCELEMENT = "cyberharclement"
    MALADIE_GRAVE = "maladie_grave"
    RISQUE_ENFANT = "risque_enfant"


@dataclass(kw_only=True)
class Identite(Donnees):
    prenom: str
    age: int
    ville: str
    situation: str


@dataclass(kw_only=True)
class Style(Donnees):
    longueur_moyenne_mots: int
    taux_fautes: float
    ponctuation: str
    exemples_messages: list[str]


@dataclass(kw_only=True)
class EtatEntree(Donnees):
    ouverture: int
    premier_message: str
    ne_dira_pas_sans_confiance: list[str]


@dataclass(kw_only=True)
class Meta(Donnees):
    modele: str
    seed: int
    version_axes: str


@dataclass(kw_only=True)
class FichePersona(Donnees):
    version_schema: str = "1.0"
    mineur: bool
    identite: Identite
    histoire: str
    style: Style
    etat_entree: EtatEntree
    objectifs: list[str]
    declencheurs_decrochage: list[str]
    system_prompt: str
    meta: Meta

    def __post_init__(self):
        if self.mineur or self.identite.age < 18:
            raise ValueError("persona mineur : interdit dans ce jeu de simulation")


@dataclass(kw_only=True)
class VecteurAxes(Donnees):
    version_axes: str = "1.0"
    position: Position
    motif_entree: MotifEntree
    resistance: Resistance
    litteratie_ia: LitteratieIA
    style_ecriture: StyleEcriture
    regularite: Regularite
    densite_emotionnelle: DensiteEmotionnelle
    cas_sensible: CasSensible
    adversarial: bool
    mineur: bool

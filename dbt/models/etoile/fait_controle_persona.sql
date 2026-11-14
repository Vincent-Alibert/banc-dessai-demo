-- Contrôles Python du persona, une ligne par run de dialogue contrôlé (banc/personas/controles_persona.py) :
-- longueur des messages écrits par le modèle persona sur tout le dialogue, comparée à la fiche ;
-- statut de fin vu par le contrôle ; forme des décisions du persona v2 ; cohérence du départ avec son
-- message (départ incohérent : QUIT_NEGATIVE dont le message continue la conversation) ; secret révélé dès le tour 1.
-- Indicateurs à lire run par run : aucun score global de fidélité.
select s.run_id::text,
       s.campagne::text as campagne_id,
       s.version_controles::text,
       s.controle_le::timestamptz,
       (s.c -> 'longueur' ->> 'messages_ecrits')::smallint as messages_ecrits,
       (s.c -> 'longueur' ->> 'moyenne_mots')::numeric(6,1) as mots_moyenne,
       (s.c -> 'longueur' ->> 'maximum_mots')::smallint as mots_maximum,
       (s.c -> 'longueur' ->> 'part_courtes')::numeric(4,2) as part_reponses_courtes,
       (s.c -> 'longueur' ->> 'seuil_court_mots')::smallint as seuil_court_mots,
       (s.c -> 'longueur' ->> 'coupes_au_plafond')::smallint as coupes_au_plafond,
       (s.c -> 'longueur' ->> 'attendue_mots')::smallint as mots_attendus_fiche,
       (s.c -> 'longueur' ->> 'source_attendue')::text as source_mots_attendus,
       (s.c -> 'longueur' ->> 'plafond_mots')::smallint as plafond_mots,
       (s.c -> 'longueur' ->> 'ratio_moyenne_attendue')::numeric(6,2) as ratio_moyenne_attendue,
       (s.c -> 'longueur' ->> 'trop_long')::boolean as trop_long,
       (s.c -> 'fin' ->> 'statut_fin')::text as statut_fin_controle,
       (s.c -> 'fin' ->> 'statut_derive')::boolean as statut_derive,
       (s.c -> 'fin' ->> 'decisions_stricte')::smallint as decisions_stricte,
       (s.c -> 'fin' ->> 'decisions_toleree')::smallint as decisions_toleree,
       (s.c -> 'fin' ->> 'decisions_repli')::smallint as decisions_repli,
       -- relance de la seule decision (16 octobre 2026) ; null pour les runs anterieurs
       (s.c -> 'fin' ->> 'decisions_relance')::smallint as decisions_relance,
       (s.c -> 'fin' ->> 'relances')::smallint as relances_decision,
       -- coherence du depart QUIT_NEGATIVE avec son message non envoye (controles 1.1, 17 octobre 2026) :
       -- null sans un tel depart ou pour un controle anterieur ; departs ecartes : persona v2.2 seulement
       -- categorie du depart (controles 1.2) : coherent (abandon), incoherent, ambigu (ni l'un ni l'autre, a relire)
       (s.c -> 'fin' ->> 'depart_categorie')::text as depart_categorie,
       (s.c -> 'fin' ->> 'depart_incoherent')::boolean as depart_incoherent,
       (s.c -> 'fin' ->> 'depart_incoherent_raison')::text as depart_incoherent_raison,
       (s.c -> 'fin' ->> 'departs_incoherents_ecartes')::smallint as departs_incoherents_ecartes,
       (s.c -> 'secret' ->> 'elements')::smallint as secrets_dans_fiche,
       (s.c -> 'secret' ->> 'revele_tour_1')::boolean as secret_revele_tour_1,
       (s.c -> 'secret' ->> 'premier_tour_affleure')::smallint as secret_premier_tour
from {{ ref('stg_controles_persona') }} s
join {{ ref('dim_run') }} dr on dr.run_id = s.run_id

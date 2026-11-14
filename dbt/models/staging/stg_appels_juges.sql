-- Sorties des deux juges factices, avec la version de grille et le fichier du run jugé.
select
    a.fichier,
    a.campagne,
    a.juge,
    a.contenu as c,
    coalesce((a.contenu ->> 'passage')::smallint, 1) as passage,
    coalesce(a.contenu ->> 'modele', a.contenu ->> 'modele_demande') as modele_exact,
    coalesce(a.contenu ->> 'modele_demande', a.contenu ->> 'modele') as modele,
    a.contenu ->> 'version_prompt' as version_prompt,
    -- fichier du run jugé : enregistré dans la sortie du juge depuis le 11 octobre 2026 ;
    -- sinon (sorties antérieures), retrouvé par le code anonyme dans la correspondance.
    coalesce(a.contenu ->> 'fichier_run',
             (select d.value ->> 'fichier'
                from {{ source('brut', 'correspondance_notation') }} cn,
                     jsonb_each(cn.contenu -> 'dialogues') d
               where cn.campagne = a.campagne and d.key = a.contenu ->> 'id')) as fichier_run,
    -- note rejouée (banc/juges/rejouer_notes.py) : campagne où elle a été enregistrée ; null pour une note de la campagne
    null::text as rejeu_de_campagne
from {{ source('brut', 'appel_juge') }} a

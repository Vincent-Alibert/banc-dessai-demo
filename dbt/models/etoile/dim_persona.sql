-- Personas, évolution lente de type 2 : une ligne par version de fiche (identifiant + empreinte),
-- datée de sa première apparition dans un run.
-- Le référentiel ne contient que les cinq fiches synthétiques fournies.
with versions as (
    select distinct on (r.c -> 'persona' ->> 'identifiant', r.empreinte_persona)
           r.c -> 'persona' ->> 'identifiant' as identifiant,
           r.empreinte_persona as empreinte_fiche,
           r.c -> 'persona' ->> 'prenom' as prenom,
           (r.c -> 'persona' ->> 'age')::smallint as age,
           p -> 'axes' ->> 'version_axes' as version_axes,
           p -> 'axes' ->> 'position' as position,
           p -> 'axes' ->> 'motif_entree' as motif_entree,
           p -> 'axes' ->> 'resistance' as resistance,
           p -> 'axes' ->> 'litteratie_ia' as litteratie_ia,
           p -> 'axes' ->> 'style_ecriture' as style_ecriture,
           p -> 'axes' ->> 'regularite' as regularite,
           p -> 'axes' ->> 'densite_emotionnelle' as densite_emotionnelle,
           coalesce(p ->> 'cas_sensible', p -> 'axes' ->> 'cas_sensible') as cas_sensible,
           (p -> 'axes' ->> 'adversarial')::boolean as adversarial,
           coalesce((p -> 'axes' ->> 'mineur')::boolean, false) as mineur,
           p ->> 'pilier_dominant' as pilier_dominant,
           p -> 'fiche_meta' ->> 'modele' as modele_generation,
           (p -> 'fiche_meta' ->> 'seed')::integer as graine,
           min(r.horodatage) over (partition by r.c -> 'persona' ->> 'identifiant', r.empreinte_persona) as premiere_vue_le
    from {{ ref('stg_runs') }} r, lateral (select r.c -> 'persona' as p) x
    order by r.c -> 'persona' ->> 'identifiant', r.empreinte_persona, r.horodatage
)
select (row_number() over (order by v.identifiant, v.empreinte_fiche))::integer as persona_cle,
       v.identifiant::text, empreinte_fiche::text, prenom::text, age::smallint, version_axes::text,
       position::text, motif_entree::text, resistance::text, litteratie_ia::text, style_ecriture::text,
       regularite::text, densite_emotionnelle::text, cas_sensible::text, adversarial::boolean,
       mineur::boolean, pilier_dominant::text, modele_generation::text, graine::integer,
       premiere_vue_le::timestamptz,
       coalesce(j.jeu, 'non_classe')::text as jeu
from versions v
left join {{ ref('jeux_personas') }} j on j.identifiant = v.identifiant

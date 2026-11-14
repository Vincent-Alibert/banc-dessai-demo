-- Juges : un par nom, modèle exact et version de prompt (grille).
with juges as (
    select distinct
           j.juge as nom,
           'factice'::text as type_juge,
           null::text as fournisseur,
           'simulation'::text as famille,
           j.modele,
           j.modele_exact,
           to_date(substring(j.modele_exact from '(\d{4}-\d{2}-\d{2})'), 'YYYY-MM-DD') as date_version,
           coalesce(j.modele ilike '%preview%' or j.modele_exact ilike '%preview%', false) as preversion,
           j.version_prompt
    from {{ ref('stg_appels_juges') }} j
)
select (row_number() over (order by nom, modele_exact, version_prompt))::integer as juge_cle,
       nom::text, type_juge::text, fournisseur::text, famille::text, modele::text, modele_exact::text,
       date_version::date, preversion::boolean, version_prompt::text
from juges

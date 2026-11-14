-- Campagnes simulées décrites automatiquement à partir des runs chargés.
with decrites as (
    select campagne_id, date_debut, date_fin, version_fiches, commit_application, base, objet
    from {{ ref('stg_campagnes') }}
),
vues_dans_les_runs as (
    select campagne as campagne_id,
           min(horodatage at time zone 'UTC')::date as date_debut,
           max(horodatage at time zone 'UTC')::date as date_fin,
           null::text as version_fiches, null::text as commit_application, null::text as base,
           'simulation non encore décrite' as objet
    from {{ ref('stg_runs') }}
    where campagne not in (select campagne_id from decrites)
    group by campagne
),
accord_seul as (
    select distinct campagne_id, null::date, null::date, null::text, null::text, null::text,
           'mesures d''accord seulement' as objet
    from {{ source('calcul', 'fait_accord') }}
    where campagne_id not in (select campagne_id from decrites)
      and campagne_id not in (select campagne_id from vues_dans_les_runs)
)
select campagne_id::text, date_debut::date, date_fin::date, version_fiches::text,
       commit_application::text, base::text, objet::text,
       {{ campagne_de_test('campagne_id') }}::boolean as est_test,
       -- campagne finale : la variable dbt campagne_finale (dbt_project.yml), vide tant qu'elle n'est pas fixée
       false::boolean as est_finale
from (select * from decrites union all select * from vues_dans_les_runs union all select * from accord_seul) c

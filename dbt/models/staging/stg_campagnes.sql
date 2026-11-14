-- Campagnes décrites dans runs/campagnes.md.
select
    campagne_id,
    (ligne ->> 'date_debut')::date as date_debut,
    (ligne ->> 'date_fin')::date as date_fin,
    ligne ->> 'version_fiches' as version_fiches,
    ligne ->> 'commit_application' as commit_application,
    ligne ->> 'base' as base,
    ligne ->> 'objet' as objet
from {{ source('brut', 'campagne') }}

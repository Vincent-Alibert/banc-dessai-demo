-- Contrôle repris de entrepot/charger.py : pour chaque run, le coût de Noa écrit dans le fichier
-- (dialogue + onboarding + analyse) égale la somme des lignes « noa_* » mesurées de l'étoile.
with fichier as (
    select b.fichier,
           round(coalesce((b.contenu -> 'cout' -> 'dialogue' ->> 'cout_usd')::numeric, 0)
               + coalesce((b.contenu -> 'cout' -> 'onboarding' ->> 'cout_usd')::numeric, 0)
               + coalesce((b.contenu -> 'cout' -> 'analyse' ->> 'cout_usd')::numeric, 0), 6) as fichier_usd
    from {{ source('brut', 'run') }} b
),
etoile as (
    select r.fichier_source, round(coalesce(sum(c.cout_usd), 0), 6) as etoile_usd
    from {{ ref('dim_run') }} r
    left join {{ ref('fait_cout') }} c on c.run_id = r.run_id and c.poste like 'noa_%' and not c.estime
    group by r.fichier_source
)
select f.fichier, f.fichier_usd, e.etoile_usd
from fichier f join etoile e on e.fichier_source = f.fichier
where f.fichier_usd <> e.etoile_usd

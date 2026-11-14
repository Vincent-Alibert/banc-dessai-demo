-- Unicités composées de l'étoile (clés primaires et UNIQUE du schéma d'avant dbt).
-- Une ligne renvoyée = une clé en double.
select 'dim_persona' as table_, identifiant || '|' || empreinte_fiche as cle, count(*) as n
from {{ ref('dim_persona') }} group by 1, 2 having count(*) > 1
union all
select 'dim_juge', nom || '|' || coalesce(modele_exact, '∅') || '|' || version_prompt, count(*)
from {{ ref('dim_juge') }} group by 1, 2 having count(*) > 1
union all
select 'dim_axe', axe_id || '|' || version_grille, count(*)
from {{ ref('dim_axe') }} group by 1, 2 having count(*) > 1
union all
select 'fait_tour', run_id || '|' || numero, count(*)
from {{ ref('fait_tour') }} group by 1, 2 having count(*) > 1
union all
select 'fait_note', run_id || '|' || juge_cle || '|' || axe_id || '|' || passage, count(*)
from {{ ref('fait_note') }} group by 1, 2 having count(*) > 1
union all
select 'fait_appel_juge', run_id || '|' || juge_cle || '|' || passage, count(*)
from {{ ref('fait_appel_juge') }} group by 1, 2 having count(*) > 1
union all
-- même règle qu'avant : UNIQUE NULLS NOT DISTINCT (run_id, poste, request_type, juge_cle)
select 'fait_cout', coalesce(run_id, '∅') || '|' || poste || '|' || coalesce(request_type, '∅') || '|'
       || coalesce(juge_cle::text, '∅'), count(*)
from {{ ref('fait_cout') }} group by 1, 2 having count(*) > 1

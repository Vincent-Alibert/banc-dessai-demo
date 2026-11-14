-- Relation fait_note -> dim_axe sur (axe_id, version_grille) : chaque note pointe vers la définition
-- de sa version de grille ; et chaque appel de juge valide note dans une grille archivée
-- (sinon : archiver la grille dans docs/evaluation/grilles/).
select 'note' as quoi, n.axe_id || ' ' || n.version_grille as detail
from {{ ref('fait_note') }} n
left join {{ ref('dim_axe') }} a using (axe_id, version_grille)
where a.axe_id is null
union all
select distinct 'grille absente', j.nom || ' ' || j.version_prompt
from {{ ref('fait_appel_juge') }} x
join {{ ref('dim_juge') }} j using (juge_cle)
where x.json_valide
  and not exists (select 1 from {{ ref('dim_axe') }} a
                  where a.version_grille = regexp_replace(j.version_prompt, '^grille-', ''))

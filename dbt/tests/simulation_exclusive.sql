-- Aucun mode non simulé ni appel/jugement réel dans cette installation.
select run_id as objet from {{ ref('dim_run') }}
where mode_execution is distinct from 'simulation'
   or campagne_id !~ '^S(0[1-9]|[1-9][0-9]+)$'
   or type_run is distinct from 'dialogue'
   or cout_simule_usd is null or cout_simule_usd < 0
union all
select nom from {{ ref('dim_juge') }}
where type_juge is distinct from 'factice' or nom not in ('factice', 'factice-b')

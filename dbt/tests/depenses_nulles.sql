-- Une dépense simulée n'est jamais une dépense payée ; tout appel API reste à zéro.
select run_id as objet from {{ ref('fait_cout') }}
where cout_usd is distinct from 0 or appels is distinct from 0 or jetons_total is distinct from 0
   or source is distinct from 'simulation' or estime
union all
select run_id from {{ ref('fait_appel_juge') }}
where cout_usd is distinct from 0 or jetons_entree is distinct from 0
   or jetons_sortie is distinct from 0 or essais_factures is distinct from 0

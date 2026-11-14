-- Scénario budgétaire, distinct des dépenses nulles de fait_cout.
select campagne_id, count(*)::bigint as dialogues,
       sum(cout_simule_usd)::numeric(12,6) as cout_simule_usd,
       'scénario complet C01, non proratisé ; aucune dépense'::text as provenance
from {{ ref('dim_run') }}
group by campagne_id

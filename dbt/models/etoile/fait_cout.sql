-- Dépenses de cette exécution seulement. Les valeurs simulées ne sont jamais additionnées ici.
with noa as (
    select r.run_id, r.campagne as campagne_id, null::integer as juge_cle,
           'noa_' || p.poste as poste, 'double_' || p.poste as request_type,
           (r.c -> 'cout' -> p.poste ->> 'appels')::integer as appels,
           (r.c -> 'cout' -> p.poste ->> 'tokens')::bigint as jetons_total,
           (r.c -> 'cout' -> p.poste ->> 'cout_usd')::numeric as cout_usd,
           false as estime, 'simulation'::text as source,
           0::bigint as jetons_entree, 0::bigint as jetons_sortie,
           null::numeric as cout_equivalent_estime_usd, 1 as ordre
    from {{ ref('stg_runs') }} r
    join {{ ref('dim_run') }} dr on dr.run_id = r.run_id
    cross join (values ('dialogue'), ('onboarding'), ('analyse')) p(poste)
),
persona as (
    select r.run_id, r.campagne, null::integer, 'persona', 'persona_enregistre', 0,
           0::bigint, 0::numeric, false, 'simulation', 0::bigint, 0::bigint, null::numeric, 2
    from {{ ref('stg_runs') }} r
    join {{ ref('dim_run') }} dr on dr.run_id = r.run_id
),
juges as (
    select dr.run_id, dr.campagne_id, dj.juge_cle, 'juge', 'passage_' || j.passage, 0,
           0::bigint, (j.c ->> 'cout_usd')::numeric, false, 'simulation',
           0::bigint, 0::bigint, null::numeric, 3
    from {{ ref('stg_appels_juges') }} j
    join {{ ref('dim_run') }} dr on dr.run_id || '.json' = j.fichier_run
    join {{ ref('dim_juge') }} dj on dj.nom = j.juge
       and dj.modele_exact is not distinct from j.modele_exact and dj.version_prompt = j.version_prompt
),
tout as (
    select * from noa union all select * from persona union all select * from juges
)
select (row_number() over (order by ordre, run_id, poste, request_type, juge_cle))::bigint as cout_id,
       run_id::text, campagne_id::text, juge_cle::integer, poste::text, request_type::text,
       appels::integer, jetons_total::bigint, cout_usd::numeric(12,6), estime::boolean, source::text,
       jetons_entree::bigint, jetons_sortie::bigint, cout_equivalent_estime_usd::numeric(12,6)
from tout

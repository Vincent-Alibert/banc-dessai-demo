-- Un tour de dialogue : message du persona, réponse de Noa, durées, jetons du persona.
select r.run_id::text,
       coalesce((t ->> 'n')::smallint, o::smallint)::smallint as numero,
       (t ->> 'persona')::text as persona_texte,
       (t ->> 'noa')::text as noa_texte,
       (t ->> 'noa_duree_s')::numeric(8,2) as noa_duree_s,
       (t ->> 'noa_caracteres')::integer as noa_caracteres,
       (t ->> 'noa_modele_facture')::text as noa_modele_facture,
       (t ->> 'noa_tier')::text as noa_tier,
       (t ->> 'persona_mots')::integer as persona_mots,
       (t ->> 'persona_duree_s')::numeric(8,2) as persona_duree_s,
       (t ->> 'persona_tokens_entree')::integer as persona_jetons_entree,
       (t ->> 'persona_tokens_sortie')::integer as persona_jetons_sortie,
       (case when t ? 'persona_coupe_au_plafond' and t ->> 'persona_coupe_au_plafond' is not null
             then (t ->> 'persona_coupe_au_plafond')::boolean end)::boolean as persona_coupe_au_plafond,
       -- la valeur est l'action prise ('coupee', 'reprise'), ou un booléen selon la version du banc
       (case when t ->> 'persona_derive_anglais' is null then null
             when t ->> 'persona_derive_anglais' = 'false' then false else true end)::boolean as persona_derive_anglais,
       -- persona v2 : décision prise avec ce message (CONTINUE) et sa forme (stricte, toleree, repli)
       (t ->> 'persona_decision')::text as persona_decision,
       (t ->> 'persona_decision_forme')::text as persona_decision_forme
from {{ ref('stg_runs') }} r
join {{ ref('dim_run') }} dr on dr.run_id = r.run_id,
     jsonb_array_elements(r.c -> 'tours') with ordinality as x(t, o)

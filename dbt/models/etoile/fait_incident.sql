-- Incidents des dialogues : produit (Noa), banc, fournisseur (juges).
with incidents as (
    select campagne as campagne_id, run_id, null::smallint as tour, 'interruption_produit' as type,
           'majeur' as gravite, 'produit' as origine, c ->> 'marquage' as description, null::text as reference
    from {{ ref('stg_runs') }} where c ->> 'marquage' ilike 'interrompu%'
    union all
    select campagne, run_id, null, 'refus_http', 'bloquant', 'produit', c -> 'resume' ->> 'refus_persona', null
    from {{ ref('stg_runs') }} where nullif(c -> 'resume' ->> 'refus_persona', '') is not null
    union all
    select r.campagne, r.run_id, null, 'constat', 'info', 'produit', cst, null
    from {{ ref('stg_runs') }} r, jsonb_array_elements_text(coalesce(r.c -> 'constats', '[]')) cst
    union all
    select dr.campagne_id, ft.run_id, ft.numero, 'reponse_vide', 'majeur', 'produit',
           'reponse de Noa vide au tour ' || ft.numero, null
    from {{ ref('fait_tour') }} ft join {{ ref('dim_run') }} dr using (run_id)
    where ft.noa_texte is not null and btrim(ft.noa_texte) = ''
    union all
    select dr.campagne_id, ft.run_id, ft.numero, 'coupe_plafond', 'info', 'banc',
           'message du persona coupe au plafond de mots', null
    from {{ ref('fait_tour') }} ft join {{ ref('dim_run') }} dr using (run_id)
    where ft.persona_coupe_au_plafond
    union all
    select r.campagne, r.run_id, coalesce((t ->> 'n')::smallint, o::smallint), 'derive_langue', 'mineur', 'banc',
           'derive du persona hors du francais, action du banc : ' || (t ->> 'persona_derive_anglais'), null
    from {{ ref('stg_runs') }} r, jsonb_array_elements(r.c -> 'tours') with ordinality as x(t, o)
    where t ->> 'persona_derive_anglais' is not null and t ->> 'persona_derive_anglais' <> 'false'
    union all
    -- persona v2 : décision de fin mal formée, retombée sur CONTINUE (moteur_persona.separer_decision)
    select dr.campagne_id, ft.run_id, ft.numero, 'decision_repli', 'mineur', 'banc',
           'decision du persona v2 absente ou contradictoire : repli sur CONTINUE', null
    from {{ ref('fait_tour') }} ft join {{ ref('dim_run') }} dr using (run_id)
    where ft.persona_decision_forme = 'repli'
    union all
    select campagne_id, run_id, null, 'hors_notation', 'info', 'banc', coalesce(marquage, 'run exclu de la notation'), null
    from {{ ref('dim_run') }} where statut = 'hors_notation'
    union all
    select dr.campagne_id, a.run_id, null, 'juge_echec', 'mineur', 'fournisseur',
           dj.nom || ' : ' || coalesce(a.erreur, 'reponse non valide'), null
    from {{ ref('fait_appel_juge') }} a
    join {{ ref('dim_run') }} dr using (run_id)
    join {{ ref('dim_juge') }} dj using (juge_cle)
    where not a.json_valide

)
select (row_number() over (order by campagne_id, run_id, type, tour, description))::bigint as incident_id,
       campagne_id::text, run_id::text, tour::smallint, type::text, gravite::text, origine::text,
       description::text, reference::text
from incidents

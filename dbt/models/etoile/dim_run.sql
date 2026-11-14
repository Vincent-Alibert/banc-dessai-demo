-- Runs du lanceur simulé : mêmes clés et statuts que le banc source.
-- Coût simulé séparé, sans dépense ni appel nouveau.
with r as (
    select r.*,
           coalesce((r.c ->> 'tour_fin')::smallint,
                    (select count(*) from jsonb_array_elements(coalesce(r.c -> 'tours', '[]')) t
                      where nullif(btrim(t ->> 'noa'), '') is not null)::smallint) as tour_fin_calc,
           coalesce((r.c -> 'resume' ->> 'tours_demandes')::smallint,
                    jsonb_array_length(coalesce(r.c -> 'tours', '[]'))::smallint) as tours_demandes_calc
    from {{ ref('stg_runs') }} r
)
select r.run_id::text,
       r.campagne::text as campagne_id,
       dp.persona_cle::integer,
       (r.c ->> 'bras')::text as bras,
       (r.c ->> 'ouverture')::text as ouverture,
       r.horodatage::timestamptz as horodatage_utc,
       (r.c -> 'cible' -> 'application' ->> 'commit')::text as commit_application,
       (r.c -> 'cible' ->> 'base')::text as base,
       (r.c -> 'modele_persona' ->> 'id')::text as modele_persona,
       (r.c -> 'resume' ->> 'tours_demandes')::smallint as tours_demandes,
       (r.c -> 'resume' ->> 'tours_joues')::smallint as tours_joues,
       (r.c -> 'resume' ->> 'duree_totale_s')::numeric(8,1) as duree_totale_s,
       (case when r.c ->> 'marquage' ilike 'interrompu%' then 'interrompu_produit'
             when cn.contenu is not null and code.code is null then 'hors_notation'
             else 'complet' end)::text as statut,
       (r.c ->> 'marquage')::text as marquage,
       code.code::text as code_notation,
       r.fichier::text as fichier_source,
       r.empreinte::text as empreinte_source,
       coalesce(r.c ->> 'type_run', 'dialogue')::text as type_run,
       (r.c ->> 'version_persona')::text as version_persona,
       coalesce(r.c ->> 'version_noa', r.c -> 'cible' -> 'application' ->> 'branche',
                r.c -> 'cible' -> 'application' ->> 'commit')::text as version_noa,
       coalesce(r.c ->> 'statut_fin',
                case when r.tour_fin_calc >= r.tours_demandes_calc then 'MAX_TURNS' else 'TECHNICAL_ERROR' end)::text
         as statut_fin,
       (r.c ->> 'statut_fin' is null)::boolean as statut_fin_derive,
       (r.c ->> 'motif_fin')::text as motif_fin,
       -- persona v2.2 (17 octobre 2026) : categorie du depart QUIT_NEGATIVE decidee pendant le dialogue
       -- (coherent = abandon ; ambigu = dialogue arrete, pas un abandon). Null avant v2.2 : la categorie
       -- a posteriori est dans fait_controle_persona (v_abandon prend l'une, sinon l'autre).
       (r.c -> 'depart' ->> 'categorie')::text as depart_categorie,
       r.tour_fin_calc::smallint as tour_fin,
       (r.c -> 'cout' ->> 'simule_usd')::numeric(12,6) as cout_simule_usd,
       (r.c ->> 'bras_banc')::text as mode_execution
from r
join {{ ref('dim_persona') }} dp
  on dp.identifiant = r.c -> 'persona' ->> 'identifiant'
 and dp.empreinte_fiche = r.empreinte_persona
left join {{ source('brut', 'correspondance_notation') }} cn on cn.campagne = r.campagne
left join lateral (
    select d.key as code from jsonb_each(cn.contenu -> 'dialogues') d
    where d.value ->> 'fichier' = r.run_id || '.json') code on true

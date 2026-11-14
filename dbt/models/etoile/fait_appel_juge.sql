-- Un appel de juge sur un run : durée, jetons, coût, validité de la réponse.
select dr.run_id::text,
       dj.juge_cle::integer,
       j.passage::smallint,
       (j.c ->> 'horodatage_utc')::timestamptz as appele_le,
       (j.c ->> 'duree_s')::numeric(8,1) as duree_s,
       (j.c -> 'usage' ->> 'entree')::integer as jetons_entree,
       (j.c -> 'usage' ->> 'sortie')::integer as jetons_sortie,
       (jsonb_typeof(j.c -> 'notes') = 'object')::boolean as json_valide,
       (j.c ->> 'erreur')::text as erreur,
       (j.c ->> 'reponse_brute')::text as reponse_brute,
       (j.c -> 'usage' ->> 'reflexion')::integer as jetons_reflexion,
       -- part de l'entrée servie depuis le cache et part écrite dans le cache (incluses dans jetons_entree),
       -- lues depuis le 17 octobre 2026 ; null avant (inconnu : coût compté au prix d'entrée plein)
       (j.c -> 'usage' ->> 'en_cache')::integer as jetons_en_cache,
       (j.c -> 'usage' ->> 'ecriture_cache')::integer as jetons_ecriture_cache,
       (j.c ->> 'cout_usd')::numeric(12,6) as cout_usd,
       (j.c -> 'usage' ->> 'essais_factures')::smallint as essais_factures,
       (j.c -> 'usage' ->> 'niveau_reflexion')::text as niveau_reflexion,
       -- commentaire libre de l'annotateur humain (page de notation), hors de tout calcul
       nullif(j.c -> 'notes' -> 'justifications' ->> 'commentaire', '')::text as commentaire,
       j.rejeu_de_campagne::text
from {{ ref('stg_appels_juges') }} j
join {{ ref('dim_run') }} dr on dr.run_id || '.json' = j.fichier_run
join {{ ref('dim_juge') }} dj on dj.nom = j.juge
                             and dj.modele_exact is not distinct from j.modele_exact
                             and dj.version_prompt = j.version_prompt

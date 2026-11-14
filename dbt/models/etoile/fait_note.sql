-- Une note : chaque note pointe vers la définition de SA version de grille, celle du prompt du juge.
select dr.run_id::text,
       dj.juge_cle::integer,
       ax.axe_id::text,
       j.passage::smallint,
       (j.c -> 'notes' ->> ax.axe_id)::smallint as valeur,
       ((j.c -> 'notes' ->> ax.axe_id) is null)::boolean as sans_objet,
       (j.c -> 'notes' -> 'justifications' ->> ax.axe_id)::text as justification,
       (j.c ->> 'horodatage_utc')::timestamptz as note_le,
       ax.version_grille::text,
       j.rejeu_de_campagne::text
from {{ ref('stg_appels_juges') }} j
join {{ ref('dim_run') }} dr on dr.run_id || '.json' = j.fichier_run
join {{ ref('dim_juge') }} dj on dj.nom = j.juge
                             and dj.modele_exact is not distinct from j.modele_exact
                             and dj.version_prompt = j.version_prompt
join {{ ref('dim_axe') }} ax on ax.version_grille = regexp_replace(j.version_prompt, '^grille-', '')
where jsonb_typeof(j.c -> 'notes') = 'object'
  and j.c -> 'notes' ? ax.axe_id   -- une note v1.0 n'a pas de B0 : pas de ligne, plutôt qu'un faux « sans objet »

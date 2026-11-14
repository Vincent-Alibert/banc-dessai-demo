-- Fin des dialogues (16 octobre 2026) : aucun tour fabriqué après un départ, et un statut cohérent.
--  1. départ du persona (QUIT_NEGATIVE, END_NORMAL) : aucun tour au-delà de tour_fin ;
--  2. quel que soit le statut : aucune réponse de Noa non vide au-delà de tour_fin (une erreur technique
--     garde la trace du tour en échec, sans réponse) ;
--  3. MAX_TURNS : tour_fin = tours demandés ; départ : tour_fin < tours demandés.
select 'tour apres un depart' as quoi, r.run_id, t.numero::text as detail
from {{ ref('fait_tour') }} t join {{ ref('dim_run') }} r using (run_id)
where r.statut_fin in ('QUIT_NEGATIVE', 'END_NORMAL') and t.numero > r.tour_fin
union all
select 'reponse de Noa apres tour_fin', r.run_id, t.numero::text
from {{ ref('fait_tour') }} t join {{ ref('dim_run') }} r using (run_id)
where t.numero > r.tour_fin and nullif(btrim(t.noa_texte), '') is not null
union all
select 'statut incoherent', run_id, statut_fin || ' ' || tour_fin || '/' || coalesce(tours_demandes::text, '?')
from {{ ref('dim_run') }}
where (statut_fin = 'MAX_TURNS' and tour_fin <> tours_demandes)
   or (statut_fin in ('QUIT_NEGATIVE', 'END_NORMAL') and tour_fin >= tours_demandes)

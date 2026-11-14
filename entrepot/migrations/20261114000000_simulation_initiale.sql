-- Base neuve de simulation : couche brute et table de calcul conservées des migrations source.
-- L'étoile et ses vues sont construites par dbt ; aucune donnée préexistante n'est importée.
CREATE SCHEMA banc_brut;
CREATE SCHEMA banc;

CREATE TABLE banc_brut.run (
  fichier     text PRIMARY KEY,                -- chemin relatif a runs/
  campagne    text NOT NULL CHECK (campagne ~ '^S(0[1-9]|[1-9][0-9]+)$'),
  empreinte   text NOT NULL,                   -- sha256 du fichier, tronque
  contenu     jsonb NOT NULL CHECK ((contenu ->> 'bras_banc' = 'simulation') IS TRUE)
                              CHECK ((contenu ->> 'campagne' = campagne) IS TRUE),
  charge_le   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE banc_brut.fiche_persona (
  fichier     text PRIMARY KEY,
  empreinte   text NOT NULL,
  contenu     jsonb NOT NULL,
  charge_le   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE banc_brut.appel_juge (
  fichier     text PRIMARY KEY,                -- runs/<C>/juges/<juge>/<code>[-p<N>].json
  campagne    text NOT NULL,
  juge        text NOT NULL CHECK (juge IN ('factice', 'factice-b')),
  empreinte   text NOT NULL,
  contenu     jsonb NOT NULL,
  charge_le   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE banc_brut.correspondance_notation (
  campagne    text PRIMARY KEY,                -- runs/<C>/notation/correspondance.json
  empreinte   text NOT NULL,
  contenu     jsonb NOT NULL,
  charge_le   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE banc_brut.campagne (
  campagne_id text PRIMARY KEY,                -- une ligne du tableau de runs/campagnes.md
  ligne       jsonb NOT NULL,
  charge_le   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE banc_brut.grille (
  version_grille text PRIMARY KEY,             -- docs/PROMPT-JUGE.md
  axes           jsonb NOT NULL,               -- [{axe_id, libelle, definition}, ...]
  charge_le      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE banc_brut.controle_persona (
  fichier    text PRIMARY KEY,           -- <campagne>/controles/persona.json
  campagne   text NOT NULL,
  empreinte  text NOT NULL,
  contenu    jsonb NOT NULL,
  charge_le  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE banc.fait_accord (
  campagne_id    text NOT NULL,
  version_grille text NOT NULL,
  axe_id         text NOT NULL,                 -- 'B1'..'B6', ou 'tous'
  mesure         text NOT NULL,                 -- 'alpha_ordinal', 'kappa_quadratique', ...
  juges          text NOT NULL,                 -- noms des juges compares, tries, separes par '+'
  valeur         numeric(6,4),                  -- NULL : non calculable
  unites         integer NOT NULL,
  motif          text,                          -- pourquoi NULL, le cas echeant
  calcule_le     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (campagne_id, version_grille, axe_id, mesure, juges)
);

GRANT USAGE ON SCHEMA banc TO metabase_lecture;
GRANT SELECT ON ALL TABLES IN SCHEMA banc TO metabase_lecture;
ALTER DEFAULT PRIVILEGES IN SCHEMA banc GRANT SELECT ON TABLES TO metabase_lecture;

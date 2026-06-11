-- xForge V2 migration — idempotent, runs after 01_schema.sql
-- Adds multi-provider support columns and BI materialized views.
-- xg_value and xt_value already exist in V1 schema — those ADD COLUMNs are no-ops.

-- ─── Extend fact_events with V2 provider provenance columns ────────────────

ALTER TABLE fact_events
    ADD COLUMN IF NOT EXISTS provider      TEXT  DEFAULT 'statsbomb',
    ADD COLUMN IF NOT EXISTS coord_system  TEXT  DEFAULT 'statsbomb_120x80',
    ADD COLUMN IF NOT EXISTS ingested_at   TIMESTAMPTZ DEFAULT now();

-- Partial index: fast filtering of shots from any provider
CREATE INDEX IF NOT EXISTS idx_fe_provider
    ON fact_events (provider, competition_id);

CREATE INDEX IF NOT EXISTS idx_fe_shot_v2
    ON fact_events (event_type, competition_id)
    WHERE event_type = 'Shot';

-- ─── Materialized view 1: team xG per match ────────────────────────────────

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_team_xg AS
SELECT
    COALESCE(dt.team_name, 'Unknown')                             AS team_name,
    fe.match_id,
    COUNT(*) FILTER (WHERE fe.event_type = 'Shot')                AS total_shots,
    ROUND(SUM(fe.xg_value)::NUMERIC, 4)                           AS total_xg,
    COUNT(*) FILTER (WHERE fe.outcome = 'Goal')                   AS goals_scored,
    ROUND(
        (COUNT(*) FILTER (WHERE fe.outcome = 'Goal') * 1.0
        / NULLIF(COUNT(*) FILTER (WHERE fe.event_type = 'Shot'), 0))::NUMERIC
    , 4)                                                          AS conversion_rate
FROM fact_events fe
LEFT JOIN dim_teams dt ON fe.team_id = dt.team_id
WHERE fe.event_type = 'Shot'
GROUP BY COALESCE(dt.team_name, 'Unknown'), fe.match_id;

-- COALESCE ensures no NULL in unique index — required for REFRESH CONCURRENTLY
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_team_xg
    ON mv_team_xg (team_name, match_id);

-- ─── Materialized view 2: normalised shot locations (105×68 m) ─────────────

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_shot_locations AS
SELECT
    fe.event_id,
    fe.match_id,
    dp.player_name,
    COALESCE(dt.team_name, 'Unknown')                             AS team_name,
    CASE fe.coord_system
        WHEN 'statsbomb_120x80' THEN ROUND((fe.location_x * 105.0 / 120.0)::NUMERIC, 3)
        WHEN 'opta_100x100'     THEN ROUND((fe.location_x * 105.0 / 100.0)::NUMERIC, 3)
        ELSE fe.location_x
    END                                                           AS x_norm,
    CASE fe.coord_system
        WHEN 'statsbomb_120x80' THEN ROUND((fe.location_y * 68.0  /  80.0)::NUMERIC, 3)
        WHEN 'opta_100x100'     THEN ROUND((fe.location_y * 68.0  / 100.0)::NUMERIC, 3)
        ELSE fe.location_y
    END                                                           AS y_norm,
    fe.xg_value,
    fe.outcome,
    fe.under_pressure,
    fe.minute
FROM fact_events fe
LEFT JOIN dim_players dp ON fe.player_id  = dp.player_id
LEFT JOIN dim_teams   dt ON fe.team_id    = dt.team_id
WHERE fe.event_type = 'Shot';

-- event_id is the natural unique key — required for REFRESH CONCURRENTLY
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_shot_locations_event
    ON mv_shot_locations (event_id);

CREATE INDEX IF NOT EXISTS idx_mv_shot_locations_match
    ON mv_shot_locations (match_id);

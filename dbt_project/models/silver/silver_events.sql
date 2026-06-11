-- Silver layer: coordinate normalisation to universal 105×68 metre pitch.
-- This is the single source of truth for all spatial analytics.
-- All Gold models and ML scripts consume this view — never raw fact_events.

with bronze as (
    select * from {{ ref('bronze_events') }}
),

normalised as (
    select
        event_id,
        match_id,
        team_id,
        player_id,
        event_type,
        minute,
        second,
        provider,
        coord_system,

        -- Normalise all coordinates to 105×68 metre universal pitch
        round({{ normalise_x('location_x', 'coord_system') }}::numeric, 3)     as location_x,
        round({{ normalise_y('location_y', 'coord_system') }}::numeric, 3)     as location_y,
        round({{ normalise_x('end_location_x', 'coord_system') }}::numeric, 3) as end_location_x,
        round({{ normalise_y('end_location_y', 'coord_system') }}::numeric, 3) as end_location_y,

        coalesce(outcome, 'Unknown')        as outcome,
        coalesce(under_pressure, false)     as under_pressure,
        raw_json,
        ingested_at
    from bronze
    where event_id is not null
      and match_id is not null
      and location_x is not null
      and location_y is not null
)

select * from normalised

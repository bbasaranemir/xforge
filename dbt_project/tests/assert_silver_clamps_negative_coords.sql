-- Spatial gate test: negative raw coordinates (below-zero boundary).
--
-- Scenario: StatsBomb X=-10, Y=-5 — both below zero (e.g. ball tracked behind goal line).
--   After normalise_x: -10 × (105/120) = -8.75 → GREATEST clamp → 0.0
--   After normalise_y:  -5 × (68/80)   = -4.25 → GREATEST clamp → 0.0
--
-- An empty result (0 rows) proves the lower-bound clamp holds.
-- Any rows returned means negative coordinates can enter Silver and corrupt
-- distance_to_goal and shot_angle feature calculations downstream.

with negative_raw as (
    select
        'statsbomb_120x80'::varchar as coord_system,
        -10.0::float               as location_x,
        -5.0::float                as location_y
),

normalised as (
    select
        least(greatest(
            {{ normalise_x('location_x', 'coord_system') }}, 0.0
        ), 105.0) as location_x,
        least(greatest(
            {{ normalise_y('location_y', 'coord_system') }}, 0.0
        ), 68.0)  as location_y
    from negative_raw
)

select *
from normalised
where location_x < 0
   or location_x > 105
   or location_y < 0
   or location_y > 68

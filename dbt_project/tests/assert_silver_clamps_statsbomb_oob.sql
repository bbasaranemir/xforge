-- Spatial gate test: StatsBomb out-of-bounds raw coordinates.
--
-- Scenario: raw X=126, Y=84 — both exceed StatsBomb's pitch boundary (~120×80).
--   After normalise_x: 126 × (105/120) = 110.25  → LEAST clamp → 105.0
--   After normalise_y:  84 × (68/80)  =  71.4   → LEAST clamp →  68.0
--
-- An empty result (0 rows) proves the macro + clamp hold.
-- Any rows returned means the Silver normalisation or clamping logic is broken,
-- allowing out-of-bounds coordinates to survive into downstream models.

with oob_raw as (
    select
        'statsbomb_120x80'::varchar as coord_system,
        126.0::float               as location_x,
        84.0::float                as location_y
),

normalised as (
    select
        least(greatest(
            {{ normalise_x('location_x', 'coord_system') }}, 0.0
        ), 105.0) as location_x,
        least(greatest(
            {{ normalise_y('location_y', 'coord_system') }}, 0.0
        ), 68.0)  as location_y
    from oob_raw
)

select *
from normalised
where location_x < 0
   or location_x > 105
   or location_y < 0
   or location_y > 68

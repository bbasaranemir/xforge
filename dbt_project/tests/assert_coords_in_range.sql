-- Singular test: fails (returns rows) if any normalised coordinate is outside 105×68.
-- A non-empty result means normalisation logic is broken for one or more providers.

select event_id, location_x, location_y, coord_system
from {{ ref('silver_events') }}
where location_x < 0 or location_x > 105
   or location_y < 0 or location_y > 68

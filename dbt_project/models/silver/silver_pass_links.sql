-- Pass events enriched with recipient player ID where the provider supports it.
-- StatsBomb and Wyscout both encode recipient in raw_json->pass->recipient->id.
-- Opta F24 does not include recipient, so to_player_id is NULL for Opta events.
-- This model is the foundation for mart_pass_network aggregations.

with passes as (
    select * from {{ ref('silver_passes') }}
),

fact as (
    select event_id, raw_json, provider
    from {{ source('public', 'fact_events') }}
),

linked as (
    select
        p.event_id,
        p.match_id,
        p.team_id,
        p.player_id                                                     as from_player_id,
        case f.provider
            when 'statsbomb' then (f.raw_json -> 'pass' -> 'recipient' ->> 'id')::int
            when 'wyscout'   then (f.raw_json -> 'pass' -> 'recipient' ->> 'id')::int
            else null
        end                                                             as to_player_id,
        p.pass_distance_m,
        p.is_successful,
        p.under_pressure,
        p.location_x,
        p.location_y,
        p.end_location_x,
        p.end_location_y
    from passes p
    inner join fact f on p.event_id = f.event_id
    where p.player_id is not null
)

select * from linked

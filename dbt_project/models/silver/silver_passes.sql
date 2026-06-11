-- Pass events with Euclidean distance in real metres (105×68 pitch).
-- V1 stg_passes computed distance on StatsBomb 120×80 arbitrary units.
-- V2 distances are directly comparable to GPS physical load data.

with events as (
    select * from {{ ref('silver_events') }}
    where event_type = 'Pass'
      and location_x    is not null
      and end_location_x is not null
),

passes as (
    select
        event_id,
        match_id,
        team_id,
        player_id,
        minute,
        second,
        location_x,
        location_y,
        end_location_x,
        end_location_y,
        under_pressure,
        provider,
        outcome,

        -- Pass distance in metres on 105×68 pitch
        round(
            sqrt(
                power(end_location_x - location_x, 2) +
                power(end_location_y - location_y, 2)
            )::numeric, 2
        )                                               as pass_distance_m,

        -- Successful: StatsBomb encodes success as outcome = 'Unknown' (no outcome field)
        case when outcome = 'Unknown' then true else false end  as is_successful

    from events
)

select * from passes

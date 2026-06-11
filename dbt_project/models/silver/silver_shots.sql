-- Shot events with pre-computed xG model features.
-- xg_model.py reads this view directly — no feature engineering in Python.
-- Goal centre: (105, 34) on 105×68 metric pitch. Post width: 7.32m.

with events as (
    select * from {{ ref('silver_events') }}
    where event_type = 'Shot'
),

shots as (
    select
        event_id,
        match_id,
        team_id,
        player_id,
        minute,
        second,
        location_x,
        location_y,
        outcome,
        under_pressure,
        provider,
        raw_json,

        -- Distance to goal centre (105, 34) in metres
        round(
            sqrt(
                power(location_x - 105.0, 2) +
                power(location_y - 34.0,  2)
            )::numeric, 3
        )                                               as distance_to_goal,

        -- Shot angle: arc subtended by 7.32m goal (posts at y=30.34 and y=37.66)
        round(
            abs(atan2(
                7.32 * (105.0 - location_x),
                power(105.0 - location_x, 2) +
                power(location_y - 34.0, 2) -
                power(3.66, 2)
            ))::numeric, 5
        )                                               as shot_angle,

        -- Header: StatsBomb shot.technique.name = 'Header'
        case
            when provider = 'statsbomb'
              and raw_json->'shot'->'technique'->>'name' = 'Header' then true
            when provider = 'opta'
              and raw_json->'Qualifiers' @> '[{"QualifierId": 72}]'::jsonb then true
            else false
        end                                             as is_header,

        -- Body part (StatsBomb only; Opta qualifiers vary by feed version)
        case
            when provider = 'statsbomb'
            then coalesce(raw_json->'shot'->'body_part'->>'name', 'Unknown')
            else 'Unknown'
        end                                             as body_part,

        -- Goal flag for ML target
        case when outcome = 'Goal' then 1 else 0 end   as is_goal

    from events
    where location_x is not null
      and location_y is not null
)

select * from shots

-- PPDA (Passes Per Defensive Action) per team per match.
--
-- Defensive actions = Tackle + Interception + Foul + Block + Pressure
-- Passes allowed    = all Pass events by the opponent team in the same match
--
-- Interpretation guide:
--   PPDA < 10  → High pressing intensity (aggressive, gegenpressing style)
--   PPDA 10-15 → Medium pressing intensity (balanced mid-block)
--   PPDA > 15  → Low pressing intensity (deep defensive block)
--
-- Reference: Statsbomb / 21st Club definition of PPDA for scouting reports.

with events as (
    select * from {{ ref('silver_events') }}
),

-- Count of defensive actions the team performed in this match
defensive as (
    select
        match_id,
        team_id,
        count(*) as defensive_actions
    from events
    where event_type in ('Tackle', 'Interception', 'Foul', 'Block', 'Pressure')
      and team_id is not null
    group by match_id, team_id
),

-- Count passes made by the opponent (i.e. passes team_id did NOT make)
passes_allowed as (
    select
        p.match_id,
        d.team_id,
        count(*) as passes_allowed
    from {{ ref('silver_passes') }} p
    join defensive d
      on p.match_id  = d.match_id
     and p.team_id  != d.team_id
    group by p.match_id, d.team_id
)

select
    d.match_id,
    d.team_id,
    t.team_name,
    d.defensive_actions,
    coalesce(pa.passes_allowed, 0)                                       as passes_allowed,
    -- Standard PPDA = passes_allowed / defensive_actions (lower = more pressing).
    -- Previous version had numerator and denominator swapped, causing every match
    -- to return values < 1 and be labelled 'High'.
    round(
        coalesce(pa.passes_allowed, 0)::numeric
        / nullif(d.defensive_actions, 0),
        4
    )                                                                    as ppda,
    case
        when coalesce(pa.passes_allowed, 0)::numeric
             / nullif(d.defensive_actions, 0) < 10  then 'High'
        when coalesce(pa.passes_allowed, 0)::numeric
             / nullif(d.defensive_actions, 0) < 15  then 'Medium'
        else 'Low'
    end                                                                  as pressing_intensity
from defensive d
left join passes_allowed pa
       on d.match_id = pa.match_id
      and d.team_id  = pa.team_id
left join {{ source('public', 'dim_teams') }} t
       on d.team_id = t.team_id

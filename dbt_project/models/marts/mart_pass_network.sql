-- Pass network aggregation: how many times did player A pass to player B?
-- Built from silver_pass_links which extracts recipient from raw_json.
-- Opta events are excluded (to_player_id IS NULL for F24 feeds).
-- Pairs with fewer than 2 passes are filtered to reduce noise.
--
-- Use this mart to build pass network graphs in Superset or Grafana:
--   nodes = players, edges = pass_count, weight = successful_passes

with links as (
    select * from {{ ref('silver_pass_links') }}
    where to_player_id is not null
),

agg as (
    select
        match_id,
        team_id,
        from_player_id,
        to_player_id,
        count(*)                                        as pass_count,
        count(*) filter (where is_successful)           as successful_passes,
        round(avg(pass_distance_m)::numeric, 2)         as avg_pass_distance_m,
        count(*) filter (where under_pressure)          as passes_under_pressure
    from links
    group by match_id, team_id, from_player_id, to_player_id
    having count(*) >= 2
)

select
    a.match_id,
    a.team_id,
    t.team_name,
    a.from_player_id,
    fp.player_name                                      as from_player_name,
    a.to_player_id,
    tp.player_name                                      as to_player_name,
    a.pass_count,
    a.successful_passes,
    a.avg_pass_distance_m,
    a.passes_under_pressure,
    round(
        a.successful_passes::numeric / nullif(a.pass_count, 0),
        4
    )                                                   as pass_completion_rate
from agg a
left join {{ source('public', 'dim_teams')   }} t  on a.team_id        = t.team_id
left join {{ source('public', 'dim_players') }} fp on a.from_player_id = fp.player_id
left join {{ source('public', 'dim_players') }} tp on a.to_player_id   = tp.player_id

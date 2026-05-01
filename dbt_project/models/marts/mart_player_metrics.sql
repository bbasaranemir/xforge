with passes as (
    select
        player_id,
        team_id,
        count(*)                                          as total_passes,
        sum(is_successful)                                as successful_passes,
        round(avg(is_successful)::numeric * 100, 1)       as pass_completion_pct,
        round(avg(pass_distance)::numeric, 2)             as avg_pass_distance,
        round(sum(xt_value)::numeric, 4)                  as total_xt_passes,
        round(
            case when count(*) > 0
                 then sum(xt_value) / count(*)
            end::numeric, 6
        )                                                  as avg_xt_per_pass,
        sum(under_pressure::int)                           as passes_under_pressure,
        round(avg(xp_value)::numeric, 4)                  as avg_xp
    from {{ ref('stg_passes') }}
    where player_id is not null
    group by player_id, team_id
),

shots as (
    select
        player_id,
        count(*)                                           as total_shots,
        sum(is_goal)                                       as goals,
        round(sum(xt_value)::numeric, 4)                   as total_xt_shots
    from {{ ref('stg_shots') }}
    where player_id is not null
    group by player_id
),

all_events as (
    select
        player_id,
        round(sum(coalesce(xt_value, 0))::numeric, 4)     as total_xt
    from {{ ref('stg_events') }}
    where player_id is not null
    group by player_id
)

select
    p.player_id,
    dp.player_name,
    dt.team_name,
    p.total_passes,
    p.successful_passes,
    p.pass_completion_pct,
    p.avg_pass_distance,
    p.passes_under_pressure,
    p.avg_xp,
    coalesce(s.total_shots, 0)                            as total_shots,
    coalesce(s.goals, 0)                                  as goals,
    ae.total_xt,
    p.avg_xt_per_pass
from passes p
left join shots       s  on p.player_id = s.player_id
left join all_events  ae on p.player_id = ae.player_id
left join {{ source('public', 'dim_players') }} dp on p.player_id = dp.player_id
left join {{ source('public', 'dim_teams')   }} dt on p.team_id   = dt.team_id

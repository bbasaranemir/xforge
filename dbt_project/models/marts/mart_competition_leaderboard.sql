-- Cross-competition player xT leaderboard.
-- Reads from silver_events (normalised 105×68) and joins fact_events for ML values and competition_id.

with events as (
    select
        se.event_id,
        se.player_id,
        se.match_id,
        se.event_type,
        se.outcome,
        fe.competition_id,
        fe.xt_value,
        fe.xg_value,
        fe.xp_value
    from {{ ref('silver_events') }} se
    join {{ source('public', 'fact_events') }} fe on se.event_id = fe.event_id
    where se.player_id is not null
),

player_comp as (
    select
        player_id,
        competition_id,
        round(sum(coalesce(xt_value, 0))::numeric, 4)    as total_xt,
        count(*) filter (where event_type = 'Pass')       as total_passes,
        count(*) filter (where event_type = 'Shot')       as total_shots,
        -- avg() naturally ignores NULLs
        round(avg(xp_value)::numeric, 4)                  as avg_xp,
        round(sum(coalesce(xg_value, 0))::numeric, 4)     as total_xg,
        count(*) filter (
            where event_type = 'Shot' and outcome = 'Goal'
        )                                                  as total_goals,
        count(distinct match_id)                           as matches_played
    from events
    group by player_id, competition_id
)

select
    pc.competition_id,
    dc.competition_name,
    pc.player_id,
    dp.player_name,
    pc.total_xt,
    pc.total_passes,
    pc.total_shots,
    pc.avg_xp,
    pc.total_xg,
    pc.total_goals,
    round((pc.total_goals - pc.total_xg)::numeric, 4)                        as finishing_quality,
    pc.matches_played,
    -- per-match normalisation prevents high-volume players dominating leaderboard
    round(
        case when pc.matches_played > 0
             then pc.total_xt / pc.matches_played
        end::numeric, 4
    )                                                                          as xt_per_match,
    rank() over (partition by pc.competition_id order by pc.total_xt desc)    as xt_rank,
    rank() over (
        partition by pc.competition_id
        order by (pc.total_xt / nullif(pc.matches_played, 0)) desc
    )                                                                          as xt_per_match_rank
from player_comp pc
left join {{ source('public', 'dim_players') }}      dp on pc.player_id     = dp.player_id
left join {{ source('public', 'dim_competitions') }} dc on pc.competition_id = dc.competition_id

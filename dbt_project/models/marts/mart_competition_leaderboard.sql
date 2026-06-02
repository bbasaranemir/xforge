-- Cross-competition player xT leaderboard.
-- Joins competition metadata to identify which league/tournament each row belongs to.
with player_comp as (
    select
        fe.player_id,
        fe.competition_id,
        round(sum(coalesce(fe.xt_value, 0))::numeric, 4)    as total_xt,
        count(*) filter (where fe.event_type = 'Pass')       as total_passes,
        count(*) filter (where fe.event_type = 'Shot')       as total_shots,
        -- avg() naturally ignores NULLs; coalesce(xp_value,0) would skew avg down
        round(avg(fe.xp_value)::numeric, 4)                  as avg_xp,
        -- xg_value populated only for shots; sum ignores NULLs
        round(sum(coalesce(fe.xg_value, 0))::numeric, 4)     as total_xg,
        count(distinct fe.match_id)                          as matches_played
    from {{ ref('stg_events') }} fe
    where fe.player_id is not null
    group by fe.player_id, fe.competition_id
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
    pc.matches_played,
    -- per-match normalisation prevents high-volume players dominating leaderboard
    round(
        case when pc.matches_played > 0
             then pc.total_xt / pc.matches_played
        end::numeric, 4
    )                                                                        as xt_per_match,
    rank() over (partition by pc.competition_id order by pc.total_xt desc)  as xt_rank,
    -- secondary ranking by per-match rate (fairer across different match counts)
    rank() over (
        partition by pc.competition_id
        order by (pc.total_xt / nullif(pc.matches_played, 0)) desc
    )                                                                        as xt_per_match_rank
from player_comp pc
left join {{ source('public', 'dim_players') }}         dp on pc.player_id     = dp.player_id
left join {{ source('public', 'dim_competitions') }}    dc on pc.competition_id = dc.competition_id

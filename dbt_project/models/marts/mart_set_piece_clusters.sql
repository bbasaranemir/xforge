{{
  config(
    materialized = 'table'
  )
}}

select
    spc.event_type,
    spc.cluster_label,
    spc.center_x,
    spc.center_y,
    spc.member_count
from {{ source('public', 'set_piece_clusters') }} spc

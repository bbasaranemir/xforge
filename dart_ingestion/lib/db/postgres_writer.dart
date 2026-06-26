import 'package:postgres/postgres.dart';
import '../models/unified_event.dart';

/// Writes [UnifiedEvent] records to the PostgreSQL bronze layer.
///
/// [writeEvents] batches rows into chunks of at most [_chunkSize] events and
/// issues a single multi-row INSERT per chunk, reducing 3 401 round-trips to
/// ceil(3401 / 500) = 7.  The entire batch runs inside a single transaction so
/// it is atomic — either every chunk lands or none do.
class PostgresWriter {
  final Connection _conn;

  /// Maximum rows per INSERT statement.  Kept well below libpq's ~65 535
  /// parameter limit (17 columns × 500 = 8 500 parameters per statement).
  static const int _chunkSize = 500;

  /// Number of columns sent per event (matches the INSERT column list below,
  /// excluding `ingested_at` which uses `now()`).
  static const int _colsPerEvent = 17;

  PostgresWriter._(this._conn);

  static Future<PostgresWriter> connect({
    required String host,
    required int port,
    required String database,
    required String username,
    required String password,
  }) async {
    final conn = await Connection.open(
      Endpoint(
        host: host,
        port: port,
        database: database,
        username: username,
        password: password,
      ),
      settings: const ConnectionSettings(sslMode: SslMode.disable),
    );
    return PostgresWriter._(conn);
  }

  Future<int> writeEvents(List<UnifiedEvent> events) async {
    if (events.isEmpty) return 0;

    int written = 0;

    // Split into chunks of at most _chunkSize events.
    final chunks = <List<UnifiedEvent>>[];
    for (var i = 0; i < events.length; i += _chunkSize) {
      final end =
          (i + _chunkSize < events.length) ? i + _chunkSize : events.length;
      chunks.add(events.sublist(i, end));
    }

    // Wrap all chunks in a single transaction for atomicity.
    await _conn.runTx((session) async {
      for (final chunk in chunks) {
        written += await _insertChunk(session, chunk);
      }
    });

    return written;
  }

  /// Builds and executes a single multi-row INSERT for [chunk].
  ///
  /// For a chunk of N events the generated SQL looks like:
  /// ```sql
  /// INSERT INTO fact_events (col1, ..., col17, ingested_at) VALUES
  ///   ($1::uuid, $2, ..., $15::jsonb, ..., $17, now()),
  ///   ($18::uuid, ..., $34, now()),
  ///   ...
  /// ON CONFLICT (event_id, competition_id) DO NOTHING
  /// ```
  Future<int> _insertChunk(TxSession session, List<UnifiedEvent> chunk) async {
    // Build the VALUES clause with per-row positional placeholders.
    // Columns that need explicit PG casts have them appended inline.
    final tupleSb = StringBuffer();
    for (var i = 0; i < chunk.length; i++) {
      if (i > 0) tupleSb.write(',\n  ');
      final base = i * _colsPerEvent; // 0-based offset for this row
      // Positions (1-indexed within the row):
      //  1  event_id       → uuid cast
      //  2  match_id
      //  3  competition_id
      //  4  team_id
      //  5  player_id
      //  6  event_type
      //  7  minute
      //  8  second
      //  9  location_x
      //  10 location_y
      //  11 end_location_x
      //  12 end_location_y
      //  13 outcome
      //  14 under_pressure
      //  15 raw_json       → jsonb cast
      //  16 provider
      //  17 coord_system
      tupleSb.write(
        '(\$${base + 1}::uuid,'
        '\$${base + 2},'
        '\$${base + 3},'
        '\$${base + 4},'
        '\$${base + 5},'
        '\$${base + 6},'
        '\$${base + 7},'
        '\$${base + 8},'
        '\$${base + 9},'
        '\$${base + 10},'
        '\$${base + 11},'
        '\$${base + 12},'
        '\$${base + 13},'
        '\$${base + 14},'
        '\$${base + 15}::jsonb,'
        '\$${base + 16},'
        '\$${base + 17},'
        'now())',
      );
    }

    final sql = '''
INSERT INTO fact_events (
  event_id, match_id, competition_id, team_id, player_id,
  event_type, minute, second,
  location_x, location_y, end_location_x, end_location_y,
  outcome, under_pressure, raw_json,
  provider, coord_system, ingested_at
) VALUES
  $tupleSb
ON CONFLICT (event_id, competition_id) DO NOTHING
''';

    // Flatten all event fields into a single positional parameter list.
    final params = <Object?>[];
    for (final e in chunk) {
      final m = e.toInsertMap();
      params.add(m['eventId']);        // $N+1  uuid (cast in SQL)
      params.add(m['matchId']);        // $N+2
      params.add(m['competitionId']); // $N+3
      params.add(m['teamId']);        // $N+4
      params.add(m['playerId']);      // $N+5
      params.add(m['eventType']);     // $N+6
      params.add(m['minute']);        // $N+7
      params.add(m['second']);        // $N+8
      params.add(m['locationX']);     // $N+9
      params.add(m['locationY']);     // $N+10
      params.add(m['endLocationX']); // $N+11
      params.add(m['endLocationY']); // $N+12
      params.add(m['outcome']);       // $N+13
      params.add(m['underPressure']); // $N+14
      params.add(m['rawJson']);       // $N+15 jsonb (cast in SQL)
      params.add(m['provider']);      // $N+16
      params.add(m['coordSystem']);   // $N+17
    }

    final result = await session.execute(
      Sql(sql),
      parameters: params,
    );
    return result.affectedRows;
  }

  /// Returns xG values for shot events in a given match with pagination.
  Future<List<Map<String, dynamic>>> queryXgValues(
    int matchId, {
    int limit = 200,
    int offset = 0,
  }) async {
    final result = await _conn.execute(
      Sql.named(
        'SELECT event_id, xg_value '
        'FROM fact_events '
        'WHERE match_id = @id AND xg_value IS NOT NULL '
        'ORDER BY event_id '
        'LIMIT @lim OFFSET @off',
      ),
      parameters: {'id': matchId, 'lim': limit, 'off': offset},
    );
    return result
        .map((r) => {'event_id': r[0]?.toString(), 'xg_value': r[1]})
        .toList();
  }

  /// Returns the top-10 most similar players to [playerId].
  /// When [position] is provided, results are filtered to that position group.
  Future<List<Map<String, dynamic>>> querySimilarPlayers(
    int playerId, {
    String? position,
  }) async {
    final hasPosition = position != null && position.isNotEmpty;
    final sql = Sql.named(
      'SELECT pss.similar_player_id, dp.player_name, dp.position, '
      '       pss.similarity_score, pss.rank '
      'FROM player_similarity_scores pss '
      'JOIN dim_players dp ON pss.similar_player_id = dp.player_id '
      'WHERE pss.player_id = @pid '
      '${hasPosition ? "AND dp.position = @pos " : ""}'
      'ORDER BY pss.rank '
      'LIMIT 10',
    );
    final params = <String, dynamic>{'pid': playerId};
    if (hasPosition) params['pos'] = position;

    final result = await _conn.execute(sql, parameters: params);
    return result
        .map((r) => {
              'player_id': r[0],
              'player_name': r[1]?.toString(),
              'position': r[2]?.toString(),
              'similarity_score': r[3],
              'rank': r[4],
            })
        .toList();
  }

  /// Returns replacement candidates for [playerId] with profile comparison.
  ///
  /// Joins [player_similarity_scores] with [mart_player_metrics] to produce a
  /// decision-ready payload: target stats, top-[topN] candidates, per-stat deltas,
  /// and a plain-English recommendation string.
  Future<Map<String, dynamic>> queryReplacementCandidates(
    int playerId, {
    int topN = 3,
  }) async {
    // ── Target player ──────────────────────────────────────────────────────────
    final targetResult = await _conn.execute(
      Sql.named(
        'SELECT dp.player_id, dp.player_name, dp.position, '
        '       COALESCE(m.avg_xg, 0.0)              AS avg_xg, '
        '       COALESCE(m.pass_completion_pct, 0.0)  AS pass_completion_pct, '
        '       COALESCE(m.avg_xt_per_pass, 0.0)     AS avg_xt_per_pass, '
        '       COALESCE(m.total_shots, 0)            AS total_shots '
        'FROM dim_players dp '
        'LEFT JOIN mart_player_metrics m ON dp.player_id = m.player_id '
        'WHERE dp.player_id = @pid '
        'LIMIT 1',
      ),
      parameters: {'pid': playerId},
    );

    if (targetResult.isEmpty) {
      return {'error': 'Player $playerId not found or missing metrics'};
    }

    final t = targetResult.first;
    final tXg = _safeDouble(t[3]);
    final tPc = _safeDouble(t[4]);
    final tXt = _safeDouble(t[5]);
    final target = {
      'player_id': t[0],
      'player_name': t[1]?.toString(),
      'position': t[2]?.toString(),
      'stats': {
        'avg_xg': tXg,
        'pass_completion_pct': tPc,
        'avg_xt_per_pass': tXt,
        'total_shots': t[6],
      },
    };

    // ── Candidates ─────────────────────────────────────────────────────────────
    final candResult = await _conn.execute(
      Sql.named(
        'SELECT pss.similar_player_id, pss.similarity_score, pss.rank, '
        '       dp.player_name, dp.position, '
        '       COALESCE(m.avg_xg, 0.0)              AS avg_xg, '
        '       COALESCE(m.pass_completion_pct, 0.0)  AS pass_completion_pct, '
        '       COALESCE(m.avg_xt_per_pass, 0.0)     AS avg_xt_per_pass, '
        '       COALESCE(m.total_shots, 0)            AS total_shots '
        'FROM player_similarity_scores pss '
        'JOIN dim_players dp ON pss.similar_player_id = dp.player_id '
        'LEFT JOIN mart_player_metrics m ON pss.similar_player_id = m.player_id '
        'WHERE pss.player_id = @pid '
        'ORDER BY pss.rank '
        'LIMIT @top_n',
      ),
      parameters: {'pid': playerId, 'top_n': topN},
    );

    final candidates = candResult.map((r) {
      final cXg = _safeDouble(r[5]);
      final cPc = _safeDouble(r[6]);
      final cXt = _safeDouble(r[7]);
      return {
        'player_id': r[0],
        'similarity_score': r[1],
        'rank': r[2],
        'player_name': r[3]?.toString(),
        'position': r[4]?.toString(),
        'stats': {
          'avg_xg': cXg,
          'pass_completion_pct': cPc,
          'avg_xt_per_pass': cXt,
          'total_shots': r[8],
        },
        'delta': {
          'avg_xg': double.parse((cXg - tXg).toStringAsFixed(4)),
          'pass_completion_pct': double.parse((cPc - tPc).toStringAsFixed(2)),
          'avg_xt_per_pass': double.parse((cXt - tXt).toStringAsFixed(5)),
        },
      };
    }).toList();

    // ── Recommendation string ──────────────────────────────────────────────────
    final String recommendation;
    if (candidates.isEmpty) {
      recommendation = 'No replacement candidates found for '
          '${target['player_name']}. Run player_similarity.py first.';
    } else {
      final best = candidates.first;
      final delta = best['delta'] as Map;
      final bStats = best['stats'] as Map;
      final xgDir = (delta['avg_xg'] as double) >= 0 ? 'higher' : 'lower';
      final pcDir =
          (delta['pass_completion_pct'] as double) >= 0 ? 'higher' : 'lower';
      recommendation = 'Replacing ${target['player_name']}: best match is '
          '${best['player_name']} (similarity '
          '${(best['similarity_score'] as num).toStringAsFixed(2)}). '
          'xG/shot ${(bStats['avg_xg'] as double).toStringAsFixed(3)} vs '
          '${tXg.toStringAsFixed(3)} ($xgDir), '
          'pass completion '
          '${(bStats['pass_completion_pct'] as double).toStringAsFixed(1)}% '
          'vs ${tPc.toStringAsFixed(1)}% ($pcDir).';
    }

    return {'target': target, 'candidates': candidates, 'recommendation': recommendation};
  }

  /// Returns a decision-ready match summary for [matchId].
  ///
  /// Three analytical outputs:
  ///  - xg_diff  : home_xg − away_xg — was the scoreline fair?
  ///  - pressing_intensity : PPDA-derived High/Medium/Low label per team
  ///  - key_threats : top-3 players by cumulative xT in the match
  Future<Map<String, dynamic>> queryMatchSummary(int matchId) async {
    // ── Match info + per-team xG ───────────────────────────────────────────────
    // gold_team_summary is materialized per (team_id, match_id).
    // We join twice — once for home, once for away — using dim_matches.home/away_team.
    // ::float8 casts ensure Dart receives doubles, not NUMERIC strings.
    final matchResult = await _conn.execute(
      Sql.named(
        'SELECT dm.match_id, dm.home_team, dm.away_team, '
        '       dm.home_score, dm.away_score, '
        '       COALESCE(hg.total_xg, 0.0)::float8 AS home_xg, '
        '       COALESCE(ag.total_xg, 0.0)::float8 AS away_xg '
        'FROM dim_matches dm '
        'LEFT JOIN analytics_gold.gold_team_summary hg '
        '       ON hg.match_id = dm.match_id AND hg.team_name = dm.home_team '
        'LEFT JOIN analytics_gold.gold_team_summary ag '
        '       ON ag.match_id = dm.match_id AND ag.team_name = dm.away_team '
        'WHERE dm.match_id = @mid '
        'LIMIT 1',
      ),
      parameters: {'mid': matchId},
    );

    if (matchResult.isEmpty) {
      return {'error': 'Match $matchId not found'};
    }

    final m = matchResult.first;
    final homeTeam = m[1]?.toString() ?? '';
    final awayTeam = m[2]?.toString() ?? '';
    final homeXg  = _safeDouble(m[5]);
    final awayXg  = _safeDouble(m[6]);
    final xgDiff  = double.parse((homeXg - awayXg).toStringAsFixed(4));

    // ── Pressing intensity (PPDA) per team ────────────────────────────────────
    // mart_pressing_metrics lives in analytics_analytics_marts because the dbt
    // generate_schema_name macro prefixes every custom schema with "analytics_".
    final pressingResult = await _conn.execute(
      Sql.named(
        'SELECT team_name, ppda::float8, pressing_intensity '
        'FROM analytics_analytics_marts.mart_pressing_metrics '
        'WHERE match_id = @mid',
      ),
      parameters: {'mid': matchId},
    );

    Map<String, dynamic> homePress = {'label': 'N/A', 'ppda': null};
    Map<String, dynamic> awayPress = {'label': 'N/A', 'ppda': null};
    for (final r in pressingResult) {
      final tName = r[0]?.toString() ?? '';
      final entry = {
        'label': r[2]?.toString() ?? 'N/A',
        'ppda': _safeDouble(r[1]),
      };
      if (tName == homeTeam) {
        homePress = entry;
      } else if (tName == awayTeam) {
        awayPress = entry;
      }
    }

    // ── Top-3 xT contributors ─────────────────────────────────────────────────
    // xt_value is FLOAT in fact_events — SUM returns DOUBLE PRECISION, no cast needed.
    final threatsResult = await _conn.execute(
      Sql.named(
        'SELECT dp.player_name, dt.team_name, '
        '       ROUND(SUM(fe.xt_value)::numeric, 4)::float8 AS total_xt '
        'FROM fact_events fe '
        'JOIN dim_players dp ON fe.player_id = dp.player_id '
        'JOIN dim_teams   dt ON fe.team_id   = dt.team_id '
        'WHERE fe.match_id = @mid '
        '  AND fe.xt_value > 0 '
        'GROUP BY dp.player_name, dt.team_name '
        'ORDER BY total_xt DESC '
        'LIMIT 3',
      ),
      parameters: {'mid': matchId},
    );

    final keyThreats = <Map<String, dynamic>>[];
    for (var i = 0; i < threatsResult.length; i++) {
      final r = threatsResult[i];
      keyThreats.add({
        'rank': i + 1,
        'player_name': r[0]?.toString(),
        'team': r[1]?.toString(),
        'total_xt': _safeDouble(r[2]),
      });
    }

    return {
      'match_id': matchId,
      'home_team': homeTeam,
      'away_team': awayTeam,
      'home_score': m[3],
      'away_score': m[4],
      'xg_home': homeXg,
      'xg_away': awayXg,
      'xg_diff': xgDiff,
      'pressing_intensity': {'home': homePress, 'away': awayPress},
      'key_threats': keyThreats,
    };
  }

  /// Safely converts PostgreSQL FLOAT, DOUBLE PRECISION, NUMERIC, or null to Dart double.
  /// NUMERIC columns arrive as String from the postgres driver — handle both.
  static double _safeDouble(dynamic v) {
    if (v == null) return 0.0;
    if (v is num) return v.toDouble();
    if (v is String) return double.tryParse(v) ?? 0.0;
    return 0.0;
  }

  Future<void> close() => _conn.close();
}

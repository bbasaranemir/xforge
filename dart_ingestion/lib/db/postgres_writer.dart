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

  Future<void> close() => _conn.close();
}

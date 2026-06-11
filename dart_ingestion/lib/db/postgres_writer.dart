import 'package:postgres/postgres.dart';
import '../models/unified_event.dart';

/// Writes [UnifiedEvent] records to the PostgreSQL bronze layer.
/// Uses named parameters — no string interpolation, no SQL injection risk.
class PostgresWriter {
  final Connection _conn;

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
    int written = 0;
    for (final e in events) {
      final m = e.toInsertMap();
      final result = await _conn.execute(
        Sql.named('''
          INSERT INTO fact_events (
            event_id, match_id, competition_id, team_id, player_id,
            event_type, minute, second,
            location_x, location_y, end_location_x, end_location_y,
            outcome, under_pressure, raw_json,
            provider, coord_system, ingested_at
          ) VALUES (
            @eventId::uuid, @matchId, @competitionId, @teamId, @playerId,
            @eventType, @minute, @second,
            @locationX, @locationY, @endLocationX, @endLocationY,
            @outcome, @underPressure, @rawJson::jsonb,
            @provider, @coordSystem, now()
          ) ON CONFLICT (event_id, competition_id) DO NOTHING
        '''),
        parameters: m,
      );
      // affectedRows is 0 on conflict, 1 on actual insert
      written += result.affectedRows;
    }
    return written;
  }

  Future<void> close() => _conn.close();
}

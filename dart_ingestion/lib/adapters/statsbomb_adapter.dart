import 'dart:convert';
import 'package:http/http.dart' as http;
import 'adapter_interface.dart';
import '../models/unified_event.dart';

/// Fetches StatsBomb open-data events via HTTP and maps to [UnifiedEvent].
/// Coordinates are stored as-is (StatsBomb 120×80); normalisation happens
/// in the Silver dbt layer using the [coordSystem] tag.
///
/// Retries on 5xx server errors up to [maxRetries] times with linear backoff.
/// 4xx client errors (404, etc.) fail immediately — retrying won't help.
class StatsBombAdapter implements DataAdapter {
  static const String _baseUrl =
      'https://raw.githubusercontent.com/statsbomb/open-data/master/data/events';

  final http.Client _client;
  final int maxRetries;
  final Duration retryDelay;

  StatsBombAdapter({
    http.Client? client,
    this.maxRetries = 3,
    this.retryDelay = const Duration(milliseconds: 200),
  }) : _client = client ?? http.Client();

  @override
  String get providerName => 'statsbomb';

  @override
  String get coordSystem => 'statsbomb_120x80';

  @override
  Future<List<UnifiedEvent>> fetchEvents({
    required int matchId,
    Map<String, dynamic> options = const {},
  }) async {
    final url = Uri.parse('$_baseUrl/$matchId.json');
    final competitionId = options['competition_id'] as int? ?? 0;

    for (var attempt = 1; attempt <= maxRetries; attempt++) {
      final response = await _client.get(url);

      if (response.statusCode == 200) {
        final List<dynamic> raw = jsonDecode(response.body) as List<dynamic>;
        return raw
            .map((e) => _mapEvent(e as Map<String, dynamic>, matchId, competitionId))
            .toList();
      }

      // 4xx: client error — retrying will not change the outcome
      if (response.statusCode >= 400 && response.statusCode < 500) {
        throw Exception(
            'StatsBomb fetch failed for match $matchId: HTTP ${response.statusCode}');
      }

      // 5xx: server error — retry with linear backoff
      if (attempt < maxRetries) {
        await Future.delayed(retryDelay * attempt.toDouble());
      } else {
        throw Exception(
            'StatsBomb fetch failed for match $matchId: HTTP ${response.statusCode} '
            'after $maxRetries attempts');
      }
    }

    // Unreachable — satisfies Dart's definite assignment analysis
    throw Exception('StatsBomb fetch failed for match $matchId');
  }

  UnifiedEvent _mapEvent(Map<String, dynamic> e, int matchId, int competitionId) {
    final location = e['location'] as List<dynamic>?;
    final type = e['type'] as Map<String, dynamic>?;

    List<dynamic>? endLoc =
        (e['pass'] as Map<String, dynamic>?)?['end_location'] as List<dynamic>? ??
        (e['carry'] as Map<String, dynamic>?)?['end_location'] as List<dynamic>? ??
        (e['shot'] as Map<String, dynamic>?)?['end_location'] as List<dynamic>?;

    final passOutcome =
        ((e['pass'] as Map<String, dynamic>?)?['outcome'] as Map<String, dynamic>?)?['name']
            as String?;
    final shotOutcome =
        ((e['shot'] as Map<String, dynamic>?)?['outcome'] as Map<String, dynamic>?)?['name']
            as String?;
    final duelOutcome =
        ((e['duel'] as Map<String, dynamic>?)?['outcome'] as Map<String, dynamic>?)?['name']
            as String?;
    final outcome = passOutcome ?? shotOutcome ?? duelOutcome ?? 'Unknown';

    return UnifiedEvent(
      eventId: e['id'] as String,
      matchId: matchId,
      competitionId: competitionId,
      teamId: (e['team'] as Map<String, dynamic>?)?['id'] as int?,
      playerId: (e['player'] as Map<String, dynamic>?)?['id'] as int?,
      eventType: type?['name'] as String? ?? 'Unknown',
      minute: e['minute'] as int? ?? 0,
      second: e['second'] as int? ?? 0,
      locationX: location != null ? (location[0] as num).toDouble() : null,
      locationY: location != null ? (location[1] as num).toDouble() : null,
      endLocationX: endLoc != null ? (endLoc[0] as num).toDouble() : null,
      endLocationY: endLoc != null ? (endLoc[1] as num).toDouble() : null,
      outcome: outcome,
      underPressure: e['under_pressure'] as bool? ?? false,
      provider: providerName,
      coordSystem: coordSystem,
      rawJson: e,
    );
  }
}

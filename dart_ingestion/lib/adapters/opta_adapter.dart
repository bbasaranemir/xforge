import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'adapter_interface.dart';
import '../models/unified_event.dart';

/// Parses Opta F24 JSON feed and maps to [UnifiedEvent].
/// Opta uses a 100×100 coordinate system; the Silver dbt layer normalises
/// to 105×68 using the [coordSystem] tag on each row.
class OptaAdapter implements DataAdapter {
  // Opta F24 TypeId → canonical event name
  static const Map<int, String> _typeMap = {
    1: 'Pass',
    2: 'Offside Pass',
    3: 'Take On',
    4: 'Foul',
    5: 'Out',
    6: 'Corner Awarded',
    7: 'Tackle',
    8: 'Interception',
    9: 'Turnover',
    10: 'Save',
    11: 'Claim',
    12: 'Clearance',
    13: 'Miss',
    14: 'Post',
    15: 'Attempt Saved',
    16: 'Shot',
    17: 'Goal',
    18: 'Card',
    19: 'Player Off',
    20: 'Player On',
    35: 'Clearance Off Line',
    41: 'Ball Recovery',
    49: 'Ball Touch',
    50: 'Carry',
    54: 'High Press',
    61: 'Chance Missed',
    74: 'Keeper Sweeper',
  };

  @override
  String get providerName => 'opta';

  @override
  String get coordSystem => 'opta_100x100';

  @override
  Future<List<UnifiedEvent>> fetchEvents({
    required int matchId,
    Map<String, dynamic> options = const {},
  }) async {
    final Map<String, dynamic> data;

    // Accept either a local file path or a remote URL via options
    if (options.containsKey('file_path')) {
      final content = await File(options['file_path'] as String).readAsString();
      data = jsonDecode(content) as Map<String, dynamic>;
    } else if (options.containsKey('url')) {
      final response = await http.get(Uri.parse(options['url'] as String));
      if (response.statusCode != 200) {
        throw Exception('Opta fetch failed: HTTP ${response.statusCode}');
      }
      data = jsonDecode(response.body) as Map<String, dynamic>;
    } else {
      throw ArgumentError(
          'OptaAdapter requires options["file_path"] or options["url"]');
    }

    final competitionId = options['competition_id'] as int? ?? 0;
    final events = data['Events'] as List<dynamic>? ?? [];
    return events
        .map((e) => _mapEvent(e as Map<String, dynamic>, matchId, competitionId))
        .toList();
  }

  UnifiedEvent _mapEvent(Map<String, dynamic> e, int matchId, int competitionId) {
    final typeId = e['TypeId'] as int? ?? 0;
    final qualifiers = e['Qualifiers'] as List<dynamic>? ?? [];

    // under_pressure: Opta QualifierId 72
    final underPressure =
        qualifiers.any((q) => (q as Map)['QualifierId'] == 72);

    // Outcome: Opta uses 1 = success, 0 = failure
    final outcomeVal = e['Outcome'] as int? ?? 0;
    final outcome = outcomeVal == 1 ? 'Unknown' : 'Fail';

    return UnifiedEvent(
      eventId: e['EventId']?.toString() ?? '${matchId}_${e['Min']}_${e['Sec']}',
      matchId: matchId,
      competitionId: competitionId,
      teamId: e['TeamId'] as int?,
      playerId: e['PlayerId'] as int?,
      eventType: _typeMap[typeId] ?? 'Unknown',
      minute: e['Min'] as int? ?? 0,
      second: e['Sec'] as int? ?? 0,
      locationX: (e['X'] as num?)?.toDouble(),
      locationY: (e['Y'] as num?)?.toDouble(),
      endLocationX: (e['EndX'] as num?)?.toDouble(),
      endLocationY: (e['EndY'] as num?)?.toDouble(),
      outcome: outcome,
      underPressure: underPressure,
      provider: providerName,
      coordSystem: coordSystem,
      rawJson: e,
    );
  }
}

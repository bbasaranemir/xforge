import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'adapter_interface.dart';
import '../models/unified_event.dart';

/// Parses Wyscout v3 JSON event feed and maps to [UnifiedEvent].
/// Wyscout uses a 100×100 coordinate system; the Silver dbt layer normalises
/// to 105×68 using the [coordSystem] tag on each row.
///
/// Accepts data from a local file ([options['file_path']]) or an HTTP URL
/// ([options['url']]). When fetching via URL, an optional Bearer token may be
/// passed as [options['api_key']].
class WyscoutAdapter implements DataAdapter {
  static const Map<String, String> _typeMap = {
    'pass':           'Pass',
    'shot':           'Shot',
    'dribble':        'Carry',
    'carry':          'Carry',
    'clearance':      'Clearance',
    'foul':           'Foul',
    'duel':           'Duel',
    'interception':   'Interception',
    'pressure':       'Pressure',
    'touch':          'Ball Touch',
    'blocked_shot':   'Block',
    'goal_kick':      'Goal Kick',
    'corner':         'Corner',
    'throw_in':       'Throw In',
    'free_kick':      'Free Kick',
    'offside':        'Offside',
    'goalkeeper_reflex': 'Goalkeeper Save',
  };

  final http.Client _client;

  WyscoutAdapter({http.Client? client}) : _client = client ?? http.Client();

  /// Validates that a URL is HTTPS and does not target internal/cloud-metadata hosts.
  static void _validateUrl(String url) {
    final uri = Uri.parse(url);
    if (uri.scheme != 'https') {
      throw ArgumentError('Only HTTPS URLs are allowed');
    }
    const _blockedHosts = {'169.254.169.254', 'localhost', '127.0.0.1', '0.0.0.0'};
    if (_blockedHosts.contains(uri.host) ||
        uri.host.startsWith('192.168.') ||
        uri.host.startsWith('10.') ||
        uri.host.startsWith('172.')) {
      throw ArgumentError('URL targets a blocked host: ${uri.host}');
    }
  }

  @override
  String get providerName => 'wyscout';

  @override
  String get coordSystem => 'wyscout_100x100';

  @override
  Future<List<UnifiedEvent>> fetchEvents({
    required int matchId,
    Map<String, dynamic> options = const {},
  }) async {
    final Map<String, dynamic> data;

    if (options.containsKey('file_path')) {
      final content = await File(options['file_path'] as String).readAsString();
      data = jsonDecode(content) as Map<String, dynamic>;
    } else if (options.containsKey('url')) {
      final url = options['url'] as String;
      _validateUrl(url);
      final apiKey = options['api_key'] as String?;
      final headers = <String, String>{};
      if (apiKey != null && apiKey.isNotEmpty) {
        if (!RegExp(r'^[\w\-\.]{8,256}$').hasMatch(apiKey)) {
          throw ArgumentError('Invalid api_key format');
        }
        headers['Authorization'] = 'Bearer $apiKey';
      }
      final response = await _client.get(Uri.parse(url), headers: headers);
      if (response.statusCode != 200) {
        throw Exception('Wyscout fetch failed: HTTP ${response.statusCode}');
      }
      data = jsonDecode(response.body) as Map<String, dynamic>;
    } else {
      throw ArgumentError(
          'WyscoutAdapter requires options["file_path"] or options["url"]');
    }

    final competitionId = options['competition_id'] as int? ?? 0;
    final events = data['events'] as List<dynamic>? ?? [];
    return events
        .map((e) => _mapEvent(e as Map<String, dynamic>, matchId, competitionId))
        .toList();
  }

  UnifiedEvent _mapEvent(
      Map<String, dynamic> e, int matchId, int competitionId) {
    final primaryType =
        (e['type'] as Map<String, dynamic>?)?['primary'] as String? ?? '';
    final secondary =
        (e['type'] as Map<String, dynamic>?)?['secondary'] as List<dynamic>? ??
            [];

    // under_pressure is carried as a secondary event type tag in Wyscout v3
    final underPressure = secondary.contains('under_pressure');

    final location = e['location'] as Map<String, dynamic>?;

    // Resolve end location from whichever sub-event has one
    final passData = e['pass'] as Map<String, dynamic>?;
    final shotData = e['shot'] as Map<String, dynamic>?;
    final carryData = e['carry'] as Map<String, dynamic>?;
    final endLoc = (passData?['endLocation'] ??
        shotData?['endLocation'] ??
        carryData?['endLocation']) as Map<String, dynamic>?;

    // Outcome: pass.accurate → 'Success'; shot.isGoal → 'Goal'; shot.onTarget → 'Saved'
    String outcome = 'Unknown';
    if (passData != null) {
      outcome = (passData['accurate'] as bool? ?? false) ? 'Success' : 'Fail';
    } else if (shotData != null) {
      if (shotData['isGoal'] as bool? ?? false) {
        outcome = 'Goal';
      } else if (shotData['onTarget'] as bool? ?? false) {
        outcome = 'Saved';
      } else {
        outcome = 'Off Target';
      }
    }

    return UnifiedEvent(
      eventId:       e['id']?.toString() ?? '${matchId}_${e['minute']}_${e['second']}',
      matchId:       matchId,
      competitionId: competitionId,
      teamId:        (e['team'] as Map<String, dynamic>?)?['id'] as int?,
      playerId:      (e['player'] as Map<String, dynamic>?)?['id'] as int?,
      eventType:     _typeMap[primaryType] ?? 'Unknown',
      minute:        (e['minute'] as num?)?.toInt() ?? 0,
      second:        (e['second'] as num?)?.toInt() ?? 0,
      locationX:     (location?['x'] as num?)?.toDouble(),
      locationY:     (location?['y'] as num?)?.toDouble(),
      endLocationX:  (endLoc?['x'] as num?)?.toDouble(),
      endLocationY:  (endLoc?['y'] as num?)?.toDouble(),
      outcome:       outcome,
      underPressure: underPressure,
      provider:      providerName,
      coordSystem:   coordSystem,
      rawJson:       e,
    );
  }
}

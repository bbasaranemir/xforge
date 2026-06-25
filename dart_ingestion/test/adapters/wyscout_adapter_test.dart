import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:test/test.dart';
import '../../lib/adapters/wyscout_adapter.dart';

// ---------------------------------------------------------------------------
// Minimal Wyscout v3 event factory
// ---------------------------------------------------------------------------

Map<String, dynamic> _wyscoutEvent({
  int id = 1,
  String primaryType = 'pass',
  List<String> secondary = const [],
  Map<String, dynamic>? location,
  Map<String, dynamic>? pass,
  Map<String, dynamic>? shot,
  int? teamId,
  int? playerId,
  int minute = 5,
  int second = 10,
}) =>
    {
      'id': id,
      'type': {'primary': primaryType, 'secondary': secondary},
      if (teamId != null) 'team': {'id': teamId},
      if (playerId != null) 'player': {'id': playerId},
      'minute': minute,
      'second': second,
      if (location != null) 'location': location,
      if (pass != null) 'pass': pass,
      if (shot != null) 'shot': shot,
    };

WyscoutAdapter _adapter({http.Client? client}) =>
    WyscoutAdapter(client: client ?? http.Client());

Future<File> _writeTempFile(Map<String, dynamic> content) async {
  final dir = await Directory.systemTemp.createTemp('wyscout_test_');
  final file = File('${dir.path}/events.json');
  await file.writeAsString(jsonEncode(content));
  return file;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  group('WyscoutAdapter — provider metadata', () {
    test('providerName is wyscout', () {
      expect(_adapter().providerName, 'wyscout');
    });

    test('coordSystem is wyscout_100x100', () {
      expect(_adapter().coordSystem, 'wyscout_100x100');
    });
  });

  group('WyscoutAdapter — coordinate mapping', () {
    test('location x and y map to locationX and locationY', () async {
      final tmp = await _writeTempFile({
        'events': [
          _wyscoutEvent(location: {'x': 50.0, 'y': 30.0})
        ]
      });

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.locationX, 50.0);
      expect(events.first.locationY, 30.0);
      await tmp.delete();
    });

    test('missing location produces null coordinates', () async {
      final tmp =
          await _writeTempFile({'events': [_wyscoutEvent()]});

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.locationX, isNull);
      expect(events.first.locationY, isNull);
      await tmp.delete();
    });

    test('pass endLocation maps to endLocationX and endLocationY', () async {
      final tmp = await _writeTempFile({
        'events': [
          _wyscoutEvent(
            pass: {'accurate': true, 'endLocation': {'x': 70.0, 'y': 45.0}},
          )
        ]
      });

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.endLocationX, 70.0);
      expect(events.first.endLocationY, 45.0);
      await tmp.delete();
    });
  });

  group('WyscoutAdapter — event type mapping', () {
    test('primary type "pass" maps to Pass', () async {
      final tmp =
          await _writeTempFile({'events': [_wyscoutEvent(primaryType: 'pass')]});

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.eventType, 'Pass');
      await tmp.delete();
    });

    test('primary type "shot" maps to Shot', () async {
      final tmp =
          await _writeTempFile({'events': [_wyscoutEvent(primaryType: 'shot')]});

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.eventType, 'Shot');
      await tmp.delete();
    });

    test('unknown primary type maps to Unknown — no crash', () async {
      final tmp = await _writeTempFile(
          {'events': [_wyscoutEvent(primaryType: 'some_future_type')]});

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.eventType, 'Unknown');
      await tmp.delete();
    });
  });

  group('WyscoutAdapter — under-pressure detection', () {
    test('secondary list containing under_pressure sets flag true', () async {
      final tmp = await _writeTempFile({
        'events': [
          _wyscoutEvent(secondary: ['simple_pass', 'under_pressure'])
        ]
      });

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.underPressure, isTrue);
      await tmp.delete();
    });

    test('secondary list without under_pressure keeps flag false', () async {
      final tmp = await _writeTempFile({
        'events': [_wyscoutEvent(secondary: ['simple_pass'])]
      });

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.underPressure, isFalse);
      await tmp.delete();
    });
  });

  group('WyscoutAdapter — outcome mapping', () {
    test('accurate pass produces Success outcome', () async {
      final tmp = await _writeTempFile({
        'events': [
          _wyscoutEvent(pass: {'accurate': true, 'endLocation': null})
        ]
      });

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.outcome, 'Success');
      await tmp.delete();
    });

    test('inaccurate pass produces Fail outcome', () async {
      final tmp = await _writeTempFile({
        'events': [
          _wyscoutEvent(pass: {'accurate': false})
        ]
      });

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.outcome, 'Fail');
      await tmp.delete();
    });

    test('goal shot produces Goal outcome', () async {
      final tmp = await _writeTempFile({
        'events': [
          _wyscoutEvent(
              primaryType: 'shot',
              shot: {'isGoal': true, 'onTarget': true})
        ]
      });

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.outcome, 'Goal');
      await tmp.delete();
    });

    test('on-target non-goal shot produces Saved outcome', () async {
      final tmp = await _writeTempFile({
        'events': [
          _wyscoutEvent(
              primaryType: 'shot',
              shot: {'isGoal': false, 'onTarget': true})
        ]
      });

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.outcome, 'Saved');
      await tmp.delete();
    });
  });

  group('WyscoutAdapter — provider and coord tags', () {
    test('provider is always wyscout', () async {
      final tmp =
          await _writeTempFile({'events': [_wyscoutEvent()]});

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.provider, 'wyscout');
      await tmp.delete();
    });

    test('coordSystem is always wyscout_100x100', () async {
      final tmp =
          await _writeTempFile({'events': [_wyscoutEvent()]});

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events.first.coordSystem, 'wyscout_100x100');
      await tmp.delete();
    });
  });

  group('WyscoutAdapter — matchId and competitionId propagation', () {
    test('matchId from request is set on every event', () async {
      final tmp = await _writeTempFile({
        'events': [_wyscoutEvent(id: 99), _wyscoutEvent(id: 100)]
      });

      final events = await _adapter().fetchEvents(
          matchId: 777, options: {'file_path': tmp.path});

      expect(events.every((e) => e.matchId == 777), isTrue);
      await tmp.delete();
    });

    test('competition_id option propagates to all events', () async {
      final tmp = await _writeTempFile({
        'events': [_wyscoutEvent()]
      });

      final events = await _adapter().fetchEvents(
          matchId: 1,
          options: {'file_path': tmp.path, 'competition_id': 43});

      expect(events.first.competitionId, 43);
      await tmp.delete();
    });
  });

  group('WyscoutAdapter — empty and missing data', () {
    test('missing events key returns empty list — no crash', () async {
      final tmp = await _writeTempFile({'meta': 'no events here'});

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events, isEmpty);
      await tmp.delete();
    });

    test('empty events array returns empty list', () async {
      final tmp =
          await _writeTempFile({'events': <dynamic>[]});

      final events = await _adapter()
          .fetchEvents(matchId: 1, options: {'file_path': tmp.path});

      expect(events, isEmpty);
      await tmp.delete();
    });

    test('event with no id falls back to composite key', () async {
      final event = _wyscoutEvent(minute: 3, second: 45);
      event.remove('id');
      final tmp = await _writeTempFile({'events': [event]});

      final events = await _adapter()
          .fetchEvents(matchId: 9, options: {'file_path': tmp.path});

      expect(events.first.eventId, '9_3_45');
      await tmp.delete();
    });
  });

  group('WyscoutAdapter — option validation', () {
    test('neither file_path nor url throws ArgumentError', () {
      expect(
        () => _adapter().fetchEvents(matchId: 1, options: {}),
        throwsA(isA<ArgumentError>()),
      );
    });

    test('HTTP 404 via url throws Exception containing 404', () async {
      final client =
          MockClient((_) async => http.Response('Not Found', 404));

      await expectLater(
        () => _adapter(client: client).fetchEvents(
          matchId: 1,
          options: {'url': 'http://example.com/wyscout.json'},
        ),
        throwsA(isA<Exception>().having(
          (e) => e.toString(),
          'message',
          contains('404'),
        )),
      );
    });

    test('valid JSON response via url parses correctly', () async {
      final payload = jsonEncode({
        'events': [
          _wyscoutEvent(
              id: 55,
              primaryType: 'shot',
              location: {'x': 90.0, 'y': 50.0},
              shot: {'isGoal': true})
        ]
      });
      final client =
          MockClient((_) async => http.Response(payload, 200));

      final events = await _adapter(client: client).fetchEvents(
        matchId: 3,
        options: {'url': 'http://example.com/wyscout.json', 'competition_id': 1},
      );

      expect(events, hasLength(1));
      expect(events.first.eventType, 'Shot');
      expect(events.first.locationX, 90.0);
      expect(events.first.outcome, 'Goal');
    });

    test('api_key is sent as Bearer Authorization header', () async {
      http.Request? captured;
      final client = MockClient((req) async {
        captured = req;
        return http.Response(jsonEncode({'events': []}), 200);
      });

      await _adapter(client: client).fetchEvents(
        matchId: 1,
        options: {
          'url': 'http://api.wyscout.com/v3/matches/1/events',
          'api_key': 'secret-token-xyz',
        },
      );

      expect(captured?.headers['Authorization'], 'Bearer secret-token-xyz');
    });
  });

  group('WyscoutAdapter — corrupt data', () {
    test('malformed JSON file throws FormatException', () async {
      final tmp = await Directory.systemTemp
          .createTemp()
          .then((d) => File('${d.path}/corrupt.json'));
      await tmp.writeAsString('{not: valid: json{{');

      await expectLater(
        () => _adapter()
            .fetchEvents(matchId: 1, options: {'file_path': tmp.path}),
        throwsA(isA<FormatException>()),
      );

      try {
        await tmp.delete();
      } catch (_) {}
    });
  });
}

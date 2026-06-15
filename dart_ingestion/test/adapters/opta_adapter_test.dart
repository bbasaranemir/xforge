import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:test/test.dart';
import '../../lib/adapters/opta_adapter.dart';

Map<String, dynamic> _optaEvent({
  int typeId = 1,
  int? x,
  int? y,
  List<Map<String, dynamic>> qualifiers = const [],
  int outcome = 1,
}) =>
    {
      'EventId': 42,
      'TypeId': typeId,
      'Min': 5,
      'Sec': 10,
      'TeamId': 1,
      'PlayerId': 100,
      'Outcome': outcome,
      if (x != null) 'X': x,
      if (y != null) 'Y': y,
      'Qualifiers': qualifiers,
    };

OptaAdapter _adapter({http.Client? client}) =>
    OptaAdapter(client: client ?? http.Client());

void main() {
  group('OptaAdapter — coordinate mapping', () {
    test('null X and Y produce null locationX and locationY', () async {
      final tmp = await _writeTempFile({'Events': [_optaEvent()]});

      final events = await _adapter().fetchEvents(
        matchId: 1,
        options: {'file_path': tmp.path, 'competition_id': 43},
      );

      expect(events, hasLength(1));
      expect(events.first.locationX, isNull);
      expect(events.first.locationY, isNull);

      await tmp.delete();
    });

    test('present X and Y map to locationX and locationY', () async {
      final tmp =
          await _writeTempFile({'Events': [_optaEvent(x: 55, y: 40)]});

      final events = await _adapter().fetchEvents(
        matchId: 1,
        options: {'file_path': tmp.path},
      );

      expect(events.first.locationX, 55.0);
      expect(events.first.locationY, 40.0);

      await tmp.delete();
    });

    test('provider and coordSystem tags are always set correctly', () async {
      final tmp = await _writeTempFile({'Events': [_optaEvent()]});

      final events = await _adapter().fetchEvents(
        matchId: 1,
        options: {'file_path': tmp.path},
      );

      expect(events.first.provider, 'opta');
      expect(events.first.coordSystem, 'opta_100x100');

      await tmp.delete();
    });
  });

  group('OptaAdapter — TypeId mapping', () {
    test('known TypeId maps to canonical event name', () async {
      final tmp = await _writeTempFile({'Events': [_optaEvent(typeId: 16)]});

      final events = await _adapter().fetchEvents(
        matchId: 1,
        options: {'file_path': tmp.path},
      );

      expect(events.first.eventType, 'Shot');
      await tmp.delete();
    });

    test('unknown TypeId maps to Unknown — no crash', () async {
      final tmp =
          await _writeTempFile({'Events': [_optaEvent(typeId: 9999)]});

      final events = await _adapter().fetchEvents(
        matchId: 1,
        options: {'file_path': tmp.path},
      );

      expect(events.first.eventType, 'Unknown');
      await tmp.delete();
    });
  });

  group('OptaAdapter — under-pressure qualifier', () {
    test('QualifierId 72 sets underPressure to true', () async {
      final tmp = await _writeTempFile({
        'Events': [
          _optaEvent(qualifiers: [
            {'QualifierId': 72, 'Value': '1'}
          ])
        ]
      });

      final events = await _adapter().fetchEvents(
        matchId: 1,
        options: {'file_path': tmp.path},
      );

      expect(events.first.underPressure, isTrue);
      await tmp.delete();
    });

    test('absent qualifiers list sets underPressure to false', () async {
      final event = _optaEvent();
      event.remove('Qualifiers');
      final tmp = await _writeTempFile({'Events': [event]});

      final events = await _adapter().fetchEvents(
        matchId: 1,
        options: {'file_path': tmp.path},
      );

      expect(events.first.underPressure, isFalse);
      await tmp.delete();
    });
  });

  group('OptaAdapter — missing or empty data', () {
    test('missing Events key returns empty list — no crash', () async {
      final tmp = await _writeTempFile({'Meta': 'some metadata'});

      final events = await _adapter().fetchEvents(
        matchId: 1,
        options: {'file_path': tmp.path},
      );

      expect(events, isEmpty);
      await tmp.delete();
    });

    test('empty Events array returns empty list', () async {
      final tmp = await _writeTempFile({'Events': <dynamic>[]});

      final events = await _adapter().fetchEvents(
        matchId: 1,
        options: {'file_path': tmp.path},
      );

      expect(events, isEmpty);
      await tmp.delete();
    });
  });

  group('OptaAdapter — option validation', () {
    test('neither file_path nor url throws ArgumentError', () {
      expect(
        () => _adapter().fetchEvents(matchId: 1, options: {}),
        throwsA(isA<ArgumentError>()),
      );
    });

    test('HTTP 404 via url option throws Exception with status code', () async {
      final client = MockClient(
          (_) async => http.Response('Not Found', 404));

      await expectLater(
        () => _adapter(client: client).fetchEvents(
          matchId: 1,
          options: {'url': 'http://example.com/feed.json'},
        ),
        throwsA(isA<Exception>().having(
          (e) => e.toString(),
          'message',
          contains('404'),
        )),
      );
    });

    test('valid JSON response via url option parses correctly', () async {
      final payload = jsonEncode({'Events': [_optaEvent(typeId: 1, x: 30, y: 20)]});
      final client = MockClient(
          (_) async => http.Response(payload, 200));

      final events = await _adapter(client: client).fetchEvents(
        matchId: 5,
        options: {'url': 'http://example.com/feed.json', 'competition_id': 1},
      );

      expect(events, hasLength(1));
      expect(events.first.eventType, 'Pass');
      expect(events.first.locationX, 30.0);
    });
  });

  group('OptaAdapter — corrupt data', () {
    test('malformed JSON file throws FormatException', () async {
      final tmp = await Directory.systemTemp
          .createTemp()
          .then((d) => File('${d.path}/corrupt.json'));
      await tmp.writeAsString('{not valid json{{');

      expect(
        () => _adapter().fetchEvents(
          matchId: 1,
          options: {'file_path': tmp.path},
        ),
        throwsA(isA<FormatException>()),
      );

      await tmp.delete();
    });

    test('event with no EventId falls back to composite key', () async {
      final event = _optaEvent();
      event.remove('EventId');
      final tmp = await _writeTempFile({'Events': [event]});

      final events = await _adapter().fetchEvents(
        matchId: 7,
        options: {'file_path': tmp.path},
      );

      // Composite key format: matchId_Min_Sec
      expect(events.first.eventId, '7_5_10');
      await tmp.delete();
    });
  });
}

// Writes JSON content to a temp file and returns the File handle.
Future<File> _writeTempFile(Map<String, dynamic> content) async {
  final dir = await Directory.systemTemp.createTemp('opta_test_');
  final file = File('${dir.path}/events.json');
  await file.writeAsString(jsonEncode(content));
  return file;
}

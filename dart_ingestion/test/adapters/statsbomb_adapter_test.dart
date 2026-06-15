import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:test/test.dart';
import '../../lib/adapters/statsbomb_adapter.dart';

// Minimal valid StatsBomb event JSON — only required fields populated
Map<String, dynamic> _validEvent({
  String id = 'aaaa-bbbb-cccc-dddd',
  List<dynamic>? location,
  Map<String, dynamic>? type,
  bool? underPressure,
}) =>
    {
      'id': id,
      'minute': 10,
      'second': 30,
      if (location != null) 'location': location,
      if (type != null) 'type': type,
      if (underPressure != null) 'under_pressure': underPressure,
    };

StatsBombAdapter _adapter(http.Client client) => StatsBombAdapter(
      client: client,
      maxRetries: 3,
      retryDelay: Duration.zero, // no sleep in tests
    );

void main() {
  group('StatsBombAdapter — coordinate mapping', () {
    test('null location produces null locationX and locationY', () async {
      final client = MockClient((_) async => http.Response(
            jsonEncode([_validEvent()]),
            200,
          ));

      final events = await _adapter(client).fetchEvents(matchId: 1);

      expect(events, hasLength(1));
      expect(events.first.locationX, isNull);
      expect(events.first.locationY, isNull);
    });

    test('present location maps to locationX and locationY', () async {
      final client = MockClient((_) async => http.Response(
            jsonEncode([_validEvent(location: [75.0, 40.0])]),
            200,
          ));

      final events = await _adapter(client).fetchEvents(matchId: 1);

      expect(events.first.locationX, 75.0);
      expect(events.first.locationY, 40.0);
    });

    test('missing type field defaults eventType to Unknown', () async {
      final client = MockClient((_) async => http.Response(
            jsonEncode([_validEvent()]), // no 'type' key
            200,
          ));

      final events = await _adapter(client).fetchEvents(matchId: 1);

      expect(events.first.eventType, 'Unknown');
    });

    test('provider and coordSystem tags are always set correctly', () async {
      final client = MockClient((_) async => http.Response(
            jsonEncode([_validEvent()]),
            200,
          ));

      final events = await _adapter(client).fetchEvents(matchId: 1);

      expect(events.first.provider, 'statsbomb');
      expect(events.first.coordSystem, 'statsbomb_120x80');
    });
  });

  group('StatsBombAdapter — HTTP error handling', () {
    test('HTTP 404 throws Exception immediately without retry', () async {
      var callCount = 0;
      final client = MockClient((_) async {
        callCount++;
        return http.Response('Not Found', 404);
      });

      await expectLater(
        () => _adapter(client).fetchEvents(matchId: 99),
        throwsA(isA<Exception>().having(
          (e) => e.toString(),
          'message',
          contains('404'),
        )),
      );

      // 4xx must not be retried — exactly one attempt
      expect(callCount, 1);
    });

    test('HTTP 500 retries maxRetries times then throws', () async {
      var callCount = 0;
      final client = MockClient((_) async {
        callCount++;
        return http.Response('Internal Server Error', 500);
      });

      await expectLater(
        () => _adapter(client).fetchEvents(matchId: 1),
        throwsA(isA<Exception>().having(
          (e) => e.toString(),
          'message',
          allOf(contains('500'), contains('3 attempts')),
        )),
      );

      expect(callCount, 3); // retried the full maxRetries
    });

    test('HTTP 503 on first attempt succeeds after retry', () async {
      var callCount = 0;
      final client = MockClient((_) async {
        callCount++;
        if (callCount == 1) return http.Response('Service Unavailable', 503);
        return http.Response(
          jsonEncode([_validEvent(id: 'retry-success')]),
          200,
        );
      });

      final events = await _adapter(client).fetchEvents(matchId: 1);

      expect(events, hasLength(1));
      expect(callCount, 2); // failed once, succeeded on second attempt
    });
  });

  group('StatsBombAdapter — malformed response body', () {
    test('non-JSON body on HTTP 200 throws FormatException', () async {
      final client = MockClient((_) async => http.Response(
            '<html>Not JSON</html>',
            200,
          ));

      expect(
        () => _adapter(client).fetchEvents(matchId: 1),
        throwsA(isA<FormatException>()),
      );
    });

    test('JSON object instead of array on HTTP 200 throws TypeError', () async {
      final client = MockClient((_) async => http.Response(
            jsonEncode({'error': 'unexpected'}),
            200,
          ));

      expect(
        () => _adapter(client).fetchEvents(matchId: 1),
        throwsA(isA<TypeError>()),
      );
    });
  });

  group('StatsBombAdapter — outcome resolution', () {
    test('pass with no outcome field defaults to Unknown', () async {
      final event = _validEvent(
        id: 'pass-no-outcome',
        type: {'name': 'Pass'},
      );
      event['pass'] = <String, dynamic>{}; // no outcome key
      final client = MockClient((_) async =>
          http.Response(jsonEncode([event]), 200));

      final events = await _adapter(client).fetchEvents(matchId: 1);

      expect(events.first.outcome, 'Unknown');
    });

    test('under_pressure true is preserved', () async {
      final client = MockClient((_) async => http.Response(
            jsonEncode([_validEvent(underPressure: true)]),
            200,
          ));

      final events = await _adapter(client).fetchEvents(matchId: 1);

      expect(events.first.underPressure, isTrue);
    });

    test('absent under_pressure defaults to false', () async {
      final client = MockClient((_) async => http.Response(
            jsonEncode([_validEvent()]),
            200,
          ));

      final events = await _adapter(client).fetchEvents(matchId: 1);

      expect(events.first.underPressure, isFalse);
    });
  });
}

import 'dart:convert';
import 'package:test/test.dart';
import '../../lib/models/unified_event.dart';

UnifiedEvent _fullEvent() => const UnifiedEvent(
      eventId: 'test-uuid-1234',
      matchId: 42,
      competitionId: 43,
      teamId: 217,
      playerId: 5503,
      eventType: 'Pass',
      minute: 23,
      second: 45,
      locationX: 60.5,
      locationY: 34.0,
      endLocationX: 80.0,
      endLocationY: 34.0,
      outcome: 'Complete',
      underPressure: true,
      provider: 'statsbomb',
      coordSystem: 'statsbomb_120x80',
      rawJson: {'id': 'test-uuid-1234', 'type': 'Pass'},
    );

UnifiedEvent _sparseEvent() => const UnifiedEvent(
      eventId: 'sparse-uuid',
      matchId: 1,
      eventType: 'Unknown',
      minute: 0,
      second: 0,
      underPressure: false,
      provider: 'opta',
      coordSystem: 'opta_100x100',
      rawJson: {},
    );

void main() {
  group('UnifiedEvent.toInsertMap — key completeness', () {
    test('returns all required database column keys', () {
      final map = _fullEvent().toInsertMap();

      expect(map, containsKey('eventId'));
      expect(map, containsKey('matchId'));
      expect(map, containsKey('competitionId'));
      expect(map, containsKey('teamId'));
      expect(map, containsKey('playerId'));
      expect(map, containsKey('eventType'));
      expect(map, containsKey('minute'));
      expect(map, containsKey('second'));
      expect(map, containsKey('locationX'));
      expect(map, containsKey('locationY'));
      expect(map, containsKey('endLocationX'));
      expect(map, containsKey('endLocationY'));
      expect(map, containsKey('outcome'));
      expect(map, containsKey('underPressure'));
      expect(map, containsKey('provider'));
      expect(map, containsKey('coordSystem'));
      expect(map, containsKey('rawJson'));
    });

    test('scalar values round-trip correctly', () {
      final map = _fullEvent().toInsertMap();

      expect(map['eventId'], 'test-uuid-1234');
      expect(map['matchId'], 42);
      expect(map['competitionId'], 43);
      expect(map['teamId'], 217);
      expect(map['playerId'], 5503);
      expect(map['eventType'], 'Pass');
      expect(map['minute'], 23);
      expect(map['second'], 45);
      expect(map['locationX'], 60.5);
      expect(map['locationY'], 34.0);
      expect(map['underPressure'], isTrue);
      expect(map['provider'], 'statsbomb');
      expect(map['coordSystem'], 'statsbomb_120x80');
    });

    test('rawJson is serialised to a JSON string for JSONB binding', () {
      final map = _fullEvent().toInsertMap();
      final rawJsonValue = map['rawJson'] as String;

      // Must be valid JSON — round-trip parse must not throw
      final decoded = jsonDecode(rawJsonValue) as Map<String, dynamic>;
      expect(decoded['id'], 'test-uuid-1234');
    });

    test('empty rawJson serialises to empty JSON object string', () {
      final map = _sparseEvent().toInsertMap();
      expect(map['rawJson'], '{}');
    });
  });

  group('UnifiedEvent.toInsertMap — nullable fields', () {
    test('optional fields are null in map when not provided', () {
      final map = _sparseEvent().toInsertMap();

      expect(map['teamId'], isNull);
      expect(map['playerId'], isNull);
      expect(map['locationX'], isNull);
      expect(map['locationY'], isNull);
      expect(map['endLocationX'], isNull);
      expect(map['endLocationY'], isNull);
      expect(map['outcome'], isNull);
    });

    test('competitionId defaults to 0 when not provided', () {
      final map = _sparseEvent().toInsertMap();
      expect(map['competitionId'], 0);
    });
  });

  group('UnifiedEvent — provider tags', () {
    test('StatsBomb event carries correct provider and coordSystem', () {
      final event = _fullEvent();
      expect(event.provider, 'statsbomb');
      expect(event.coordSystem, 'statsbomb_120x80');
    });

    test('Opta event carries correct provider and coordSystem', () {
      final event = _sparseEvent();
      expect(event.provider, 'opta');
      expect(event.coordSystem, 'opta_100x100');
    });
  });
}

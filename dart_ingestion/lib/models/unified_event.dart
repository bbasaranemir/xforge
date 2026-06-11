import 'dart:convert';

/// Canonical event schema output by every DataAdapter.
/// The [provider] and [coordSystem] fields are load-bearing: the Silver dbt
/// layer uses them per-row to apply the correct normalisation formula.
class UnifiedEvent {
  final String eventId;
  final int matchId;
  final int competitionId;
  final int? teamId;
  final int? playerId;
  final String eventType;
  final int minute;
  final int second;
  final double? locationX;
  final double? locationY;
  final double? endLocationX;
  final double? endLocationY;
  final String? outcome;
  final bool underPressure;
  final String provider;
  final String coordSystem;
  final Map<String, dynamic> rawJson;

  const UnifiedEvent({
    required this.eventId,
    required this.matchId,
    this.competitionId = 0,
    this.teamId,
    this.playerId,
    required this.eventType,
    required this.minute,
    required this.second,
    this.locationX,
    this.locationY,
    this.endLocationX,
    this.endLocationY,
    this.outcome,
    required this.underPressure,
    required this.provider,
    required this.coordSystem,
    required this.rawJson,
  });

  Map<String, dynamic> toInsertMap() => {
        'eventId': eventId,
        'matchId': matchId,
        'competitionId': competitionId,
        'teamId': teamId,
        'playerId': playerId,
        'eventType': eventType,
        'minute': minute,
        'second': second,
        'locationX': locationX,
        'locationY': locationY,
        'endLocationX': endLocationX,
        'endLocationY': endLocationY,
        'outcome': outcome,
        'underPressure': underPressure,
        'provider': provider,
        'coordSystem': coordSystem,
        'rawJson': jsonEncode(rawJson),
      };
}

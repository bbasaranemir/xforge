import '../models/unified_event.dart';

/// Gang-of-Four Adapter Pattern contract.
/// Each data provider implements this interface once.
/// Adding a new provider (Wyscout, InStat, Hudl) requires one new file
/// and zero changes to the pipeline.
abstract class DataAdapter {
  String get providerName;
  String get coordSystem;

  /// Fetches and parses all events for [matchId].
  /// [options] carries provider-specific parameters (file paths, API keys, etc.)
  Future<List<UnifiedEvent>> fetchEvents({
    required int matchId,
    Map<String, dynamic> options = const {},
  });
}

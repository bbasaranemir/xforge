import 'dart:convert';
import 'dart:io';
import 'package:shelf/shelf.dart';
import 'package:shelf/shelf_io.dart' as shelf_io;
import 'package:shelf_router/shelf_router.dart';
import 'adapters/statsbomb_adapter.dart';
import 'adapters/opta_adapter.dart';
import 'adapters/wyscout_adapter.dart';
import 'adapters/adapter_interface.dart';
import 'db/postgres_writer.dart';
import 'middleware/bearer_auth.dart';
import 'models/unified_event.dart';

/// Logs a message to stderr with an [xforge] prefix.
/// Container runtimes (Docker, Kubernetes) collect stderr as structured logs.
void _log(String message) => stderr.writeln('[xforge] $message');

const _jsonHeaders = {'content-type': 'application/json'};
const _maxBodyBytes = 10 * 1024 * 1024; // 10 MB
const _validProviders = {'statsbomb', 'opta', 'wyscout'};

/// Opens a DB connection using environment variables.
Future<PostgresWriter> _openWriter() => PostgresWriter.connect(
      host: Platform.environment['DB_HOST'] ?? 'postgres',
      port: int.parse(Platform.environment['DB_PORT'] ?? '5432'),
      database: Platform.environment['DB_NAME'] ?? 'football_db',
      username: Platform.environment['DB_USER'] ?? 'analytics',
      password: Platform.environment['DB_PASS'] ?? 'analytics',
    );

void main() async {
  final router = Router();

  // ── Health ──────────────────────────────────────────────────────────────────
  // Always public — used by Docker/K8s liveness probes.
  router.get('/health', (_) => Response.ok('{"status":"ok"}', headers: _jsonHeaders));

  // ── Ingest ──────────────────────────────────────────────────────────────────
  router.post('/ingest', (Request request) async {
    // Guard: body size limit (DoS protection)
    final contentLength = request.contentLength;
    if (contentLength != null && contentLength > _maxBodyBytes) {
      return Response(413, body: '{"error":"Request body too large"}', headers: _jsonHeaders);
    }

    final Map<String, dynamic> body;
    try {
      body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
    } catch (_) {
      return Response(400, body: '{"error":"Invalid JSON body"}', headers: _jsonHeaders);
    }

    // Sanitise provider before logging to prevent log injection via \r\n
    final provider = (body['provider'] as String? ?? '').replaceAll(RegExp(r'[\r\n\t]'), '');
    final matchId = body['match_id'] as int?;

    if (provider.isEmpty || matchId == null) {
      return Response(400,
          body: '{"error":"provider and match_id are required"}', headers: _jsonHeaders);
    }

    if (!_validProviders.contains(provider)) {
      return Response(400, body: '{"error":"Unknown provider"}', headers: _jsonHeaders);
    }

    final DataAdapter adapter;
    switch (provider) {
      case 'statsbomb':
        adapter = StatsBombAdapter();
      case 'opta':
        adapter = OptaAdapter();
      case 'wyscout':
        adapter = WyscoutAdapter();
      default:
        return Response(400, body: '{"error":"Unknown provider"}', headers: _jsonHeaders);
    }

    final List<dynamic> events;
    try {
      events = await adapter.fetchEvents(
        matchId: matchId,
        options: Map<String, dynamic>.from(body),
      );
    } catch (e) {
      _log('Fetch failed: match=$matchId provider=$provider');
      return Response(502, body: '{"error":"upstream fetch failed"}', headers: _jsonHeaders);
    }

    final writer = await _openWriter();
    final int written;
    try {
      written = await writer.writeEvents(events.cast<UnifiedEvent>());
    } finally {
      await writer.close();
    }

    _log('match=$matchId provider=$provider written=$written');
    return Response.ok(
      jsonEncode({'provider': provider, 'match_id': matchId, 'written': written}),
      headers: _jsonHeaders,
    );
  });

  // ── REST API: xG values for a match ─────────────────────────────────────────
  router.get('/api/v1/matches/<id>/xg', (Request req, String id) async {
    final matchId = int.tryParse(id);
    if (matchId == null || matchId < 1) {
      return Response(400, body: '{"error":"Invalid match_id"}', headers: _jsonHeaders);
    }
    final writer = await _openWriter();
    try {
      final rows = await writer.queryXgValues(matchId);
      return Response.ok(
        jsonEncode({'match_id': matchId, 'events': rows}),
        headers: _jsonHeaders,
      );
    } finally {
      await writer.close();
    }
  });

  // ── REST API: similar players ────────────────────────────────────────────────
  // Optional query param: ?position=CM  filters results to that position group.
  router.get('/api/v1/players/<id>/similar', (Request req, String id) async {
    final playerId = int.tryParse(id);
    if (playerId == null || playerId < 1) {
      return Response(400, body: '{"error":"Invalid player_id"}', headers: _jsonHeaders);
    }
    final position = req.url.queryParameters['position'];
    final writer = await _openWriter();
    try {
      final rows = await writer.querySimilarPlayers(playerId, position: position);
      return Response.ok(
        jsonEncode({'player_id': playerId, 'similar': rows}),
        headers: _jsonHeaders,
      );
    } finally {
      await writer.close();
    }
  });

  // Bearer auth covers all routes; health is explicitly excluded via path check inside middleware.
  final handler = const Pipeline()
      .addMiddleware(logRequests())
      .addMiddleware(bearerAuth())
      .addHandler(router.call);

  final port = int.parse(Platform.environment['PORT'] ?? '8090');
  final server = await shelf_io.serve(handler, '0.0.0.0', port);
  final apiToken = Platform.environment['API_TOKEN'];
  if (apiToken == null || apiToken.isEmpty) {
    _log('WARNING: API_TOKEN is not set — /ingest endpoint is unauthenticated. Set API_TOKEN in production.');
  }
  _log('Service started on port ${server.port}');
}

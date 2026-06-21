import 'dart:convert';
import 'dart:io';
import 'package:shelf/shelf.dart';
import 'package:shelf/shelf_io.dart' as shelf_io;
import 'package:shelf_router/shelf_router.dart';
import 'adapters/statsbomb_adapter.dart';
import 'adapters/opta_adapter.dart';
import 'adapters/adapter_interface.dart';
import 'db/postgres_writer.dart';
import 'middleware/bearer_auth.dart';
import 'models/unified_event.dart';

/// Logs a message to stderr with an [xforge] prefix.
/// Container runtimes (Docker, Kubernetes) collect stderr as structured logs.
void _log(String message) => stderr.writeln('[xforge] $message');

void main() async {
  final router = Router();

  // Health endpoint — always public (used by Docker/K8s liveness probes)
  router.get('/health', (_) => Response.ok('{"status":"ok"}',
      headers: {'content-type': 'application/json'}));

  router.post('/ingest', (Request request) async {
    final Map<String, dynamic> body;
    try {
      body = jsonDecode(await request.readAsString()) as Map<String, dynamic>;
    } catch (_) {
      return Response(400, body: '{"error":"Invalid JSON body"}',
          headers: {'content-type': 'application/json'});
    }

    final provider = body['provider'] as String?;
    final matchId = body['match_id'] as int?;

    if (provider == null || matchId == null) {
      return Response(400,
          body: '{"error":"provider and match_id are required"}',
          headers: {'content-type': 'application/json'});
    }

    final DataAdapter adapter;
    switch (provider) {
      case 'statsbomb':
        adapter = StatsBombAdapter();
      case 'opta':
        adapter = OptaAdapter();
      default:
        return Response(400,
            body: '{"error":"Unknown provider: $provider"}',
            headers: {'content-type': 'application/json'});
    }

    final List<dynamic> events;
    try {
      events = await adapter.fetchEvents(
        matchId: matchId,
        options: Map<String, dynamic>.from(body),
      );
    } catch (e) {
      _log('Fetch failed: match=$matchId provider=$provider error=$e');
      return Response(502,
          body: '{"error":"upstream fetch failed"}',
          headers: {'content-type': 'application/json'});
    }

    final writer = await PostgresWriter.connect(
      host: Platform.environment['DB_HOST'] ?? 'postgres',
      port: int.parse(Platform.environment['DB_PORT'] ?? '5432'),
      database: Platform.environment['DB_NAME'] ?? 'football_db',
      username: Platform.environment['DB_USER'] ?? 'analytics',
      password: Platform.environment['DB_PASS'] ?? 'analytics',
    );

    final int written;
    try {
      written = await writer.writeEvents(events.cast<UnifiedEvent>());
    } finally {
      await writer.close();
    }

    _log('match=$matchId provider=$provider written=$written');

    return Response.ok(
      jsonEncode({'provider': provider, 'match_id': matchId, 'written': written}),
      headers: {'content-type': 'application/json'},
    );
  });

  // Bearer auth applied only to the ingest router, not the health endpoint
  final handler = const Pipeline()
      .addMiddleware(logRequests())
      .addMiddleware(bearerAuth())
      .addHandler(router.call);

  final port = int.parse(Platform.environment['PORT'] ?? '8090');
  final server = await shelf_io.serve(handler, '0.0.0.0', port);
  _log('Service started on port ${server.port}');
}

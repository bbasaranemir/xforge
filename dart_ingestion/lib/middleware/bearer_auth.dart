import 'dart:io';
import 'package:shelf/shelf.dart';

/// Bearer token guard for the ingestion service.
///
/// When [API_TOKEN] is not set in the environment the middleware is a no-op so
/// that CI pipelines and local development work without extra configuration.
/// Production deployments must set [API_TOKEN] to a strong random value.
Middleware bearerAuth() {
  return (Handler inner) {
    return (Request request) {
      final token = Platform.environment['API_TOKEN'];
      if (token == null || token.isEmpty) return inner(request);
      final auth = request.headers['authorization'] ?? '';
      if (auth != 'Bearer $token') {
        return Response(
          401,
          body: '{"error":"Unauthorized"}',
          headers: {'content-type': 'application/json'},
        );
      }
      return inner(request);
    };
  };
}

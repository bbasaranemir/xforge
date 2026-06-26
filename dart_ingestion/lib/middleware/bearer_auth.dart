import 'dart:io';
import 'package:shelf/shelf.dart';

/// Bearer token guard for the ingestion service.
///
/// When [API_TOKEN] is not set in the environment the middleware is a no-op so
/// that CI pipelines and local development work without extra configuration.
/// Production deployments must set [API_TOKEN] to a strong random value.
/// Constant-time string equality to prevent timing-based token oracle attacks.
bool _secureEquals(String a, String b) {
  if (a.length != b.length) return false;
  var result = 0;
  for (var i = 0; i < a.length; i++) {
    result |= a.codeUnitAt(i) ^ b.codeUnitAt(i);
  }
  return result == 0;
}

Middleware bearerAuth() {
  return (Handler inner) {
    return (Request request) {
      final token = Platform.environment['API_TOKEN'];
      if (token == null || token.isEmpty) return inner(request);
      final auth = request.headers['authorization'] ?? '';
      if (!_secureEquals(auth, 'Bearer $token')) {
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

import 'dart:collection';
import 'dart:io';
import 'package:shelf/shelf.dart';

/// In-memory sliding-window rate limiter.
///
/// Tracks request timestamps per IP address.  When a caller exceeds
/// [maxRequests] within [window], further requests receive HTTP 429.
///
/// Notes:
///   - State is per-process (not shared across replicas).
///   - For horizontal scaling, replace with Redis-backed counting.
///   - Reads X-Forwarded-For first (reverse-proxy), falls back to TCP peer.
Middleware rateLimit({
  int maxRequests = 60,
  Duration window = const Duration(minutes: 1),
}) {
  final _windows = <String, Queue<DateTime>>{};

  return (Handler inner) => (Request request) {
        final ip = _resolveIp(request);
        final now = DateTime.now();
        final cutoff = now.subtract(window);

        final timestamps = _windows.putIfAbsent(ip, Queue.new);
        // Evict timestamps outside the current window
        while (timestamps.isNotEmpty && timestamps.first.isBefore(cutoff)) {
          timestamps.removeFirst();
        }

        if (timestamps.length >= maxRequests) {
          return Response(
            429,
            body: '{"error":"Rate limit exceeded. Try again later."}',
            headers: {
              'content-type': 'application/json',
              'Retry-After': window.inSeconds.toString(),
            },
          );
        }

        timestamps.addLast(now);
        return inner(request);
      };
}

String _resolveIp(Request request) {
  final forwarded = request.headers['x-forwarded-for'];
  if (forwarded != null && forwarded.isNotEmpty) {
    return forwarded.split(',').first.trim();
  }
  final info =
      request.context['shelf.io.connection_info'] as HttpConnectionInfo?;
  return info?.remoteAddress.address ?? 'unknown';
}

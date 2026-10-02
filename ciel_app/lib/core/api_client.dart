import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'endpoints.dart';
import 'protocol.dart';

class ApiException implements Exception {
  const ApiException(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  bool get isUnauthorized => statusCode == 401;

  @override
  String toString() => message;
}

class HealthStatus {
  const HealthStatus({required this.ready, required this.authRequired});

  final bool ready;
  final bool authRequired;
}

/// REST side of the backend: /health, /skills, /tts.
class CielApi {
  CielApi(this.endpoints, this.token, {http.Client? client}) : _client = client ?? http.Client();

  final CielEndpoints endpoints;
  final String token;
  final http.Client _client;

  static const _timeout = Duration(seconds: 15);

  Map<String, String> get _authHeaders => token.isEmpty ? const {} : {'Authorization': 'Bearer $token'};

  Future<HealthStatus> health() async {
    final res = await _send(() => _client.get(endpoints.route('/health')));
    final json = _decode(res);
    return HealthStatus(ready: json['ready'] == true, authRequired: json['auth_required'] == true);
  }

  Future<SkillsResponse> skills() async {
    final res = await _send(() => _client.get(endpoints.route('/skills'), headers: _authHeaders));
    return SkillsResponse.fromJson(_decode(res));
  }

  /// MP3 bytes from the backend's shared speech normalizer, or null when there is nothing to say.
  Future<Uint8List?> tts(String text) async {
    final res = await _send(
      () => _client.post(
        endpoints.route('/tts'),
        headers: {..._authHeaders, 'Content-Type': 'application/json'},
        body: jsonEncode({'text': text}),
      ),
      timeout: const Duration(seconds: 60),
    );
    if (res.statusCode == 204 || res.bodyBytes.isEmpty) return null;
    return res.bodyBytes;
  }

  Future<http.Response> _send(Future<http.Response> Function() call, {Duration timeout = _timeout}) async {
    final http.Response res;
    try {
      res = await call().timeout(timeout);
    } catch (e) {
      throw ApiException('Cannot reach ${endpoints.display} ($e)');
    }
    if (res.statusCode == 401) {
      throw const ApiException('Access token rejected by the server', statusCode: 401);
    }
    if (res.statusCode >= 400) {
      throw ApiException('Server returned ${res.statusCode}', statusCode: res.statusCode);
    }
    return res;
  }

  Map<String, dynamic> _decode(http.Response res) {
    try {
      final body = jsonDecode(utf8.decode(res.bodyBytes));
      return body is Map ? Map<String, dynamic>.from(body) : const {};
    } catch (_) {
      throw const ApiException('Server sent an unreadable response');
    }
  }

  void close() => _client.close();
}

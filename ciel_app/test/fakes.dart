import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:ciel_app/core/api_client.dart';
import 'package:ciel_app/core/ciel_socket.dart';
import 'package:ciel_app/core/protocol.dart';
import 'package:ciel_app/core/settings_store.dart';
import 'package:ciel_app/features/voice/speaker.dart';
import 'package:ciel_app/state/ciel_controller.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

class FakeTransport implements CielTransport {
  final _messages = StreamController<ServerMessage>.broadcast(sync: true);
  final _statuses = StreamController<ConnectionStatus>.broadcast(sync: true);
  final List<String> sent = [];
  final List<Uri> connects = [];
  ConnectionStatus _status = ConnectionStatus.idle;

  List<Map<String, dynamic>> get sentJson =>
      sent.map((s) => Map<String, dynamic>.from(jsonDecode(s) as Map)).toList();

  @override
  Stream<ServerMessage> get messages => _messages.stream;

  @override
  Stream<ConnectionStatus> get statusChanges => _statuses.stream;

  @override
  ConnectionStatus get status => _status;

  void setStatus(ConnectionStatus value) {
    _status = value;
    _statuses.add(value);
  }

  void serverSends(String rawFrame) => _messages.add(ServerMessage.parse(rawFrame));

  @override
  void connect(Uri uri) {
    connects.add(uri);
    setStatus(ConnectionStatus.open);
  }

  @override
  void reconnectNow() {}

  @override
  bool send(String payload) {
    if (_status != ConnectionStatus.open) return false;
    sent.add(payload);
    return true;
  }

  @override
  Future<void> disconnect() async => setStatus(ConnectionStatus.idle);
}

class FakeSpeaker implements Speaker {
  final List<Uint8List> played = [];

  @override
  Stream<bool> get speaking => const Stream.empty();

  @override
  Future<void> play(Uint8List mp3) async => played.add(mp3);

  @override
  Future<void> stop() async {}
}

const skillsJson = {
  'ready': true,
  'skills': [
    {
      'module': 'gmail_ops',
      'category': 'external',
      'tool_count': 1,
      'tools': [
        {'name': 'send_gmail_message', 'description': 'Send an email'}
      ],
      'has_prompt': true,
    }
  ],
  'totals': {'modules': 1, 'tools': 1},
};

/// A backend double for /health, /skills and /tts that records request headers.
class FakeBackend {
  final List<http.Request> requests = [];

  http.Client get client => MockClient((request) async {
        requests.add(request);
        switch (request.url.path) {
          case '/health':
            return http.Response(jsonEncode({'status': 'ok', 'ready': true, 'auth_required': true}), 200);
          case '/skills':
            return http.Response(jsonEncode(skillsJson), 200);
          case '/tts':
            return http.Response.bytes([1, 2, 3], 200, headers: {'content-type': 'audio/mpeg'});
        }
        return http.Response('not found', 404);
      });
}

class Harness {
  Harness._(this.controller, this.transport, this.backend, this.speaker, this.secrets);

  final CielController controller;
  final FakeTransport transport;
  final FakeBackend backend;
  final FakeSpeaker speaker;
  final MemorySecretStore secrets;

  static Future<Harness> create({Map<String, Object> prefs = const {}, String token = 'secret-token'}) async {
    SharedPreferences.setMockInitialValues({'server_url': 'https://ciel.example.com', ...prefs});
    final secrets = MemorySecretStore()..values['api_token'] = token;
    final transport = FakeTransport();
    final backend = FakeBackend();
    final speaker = FakeSpeaker();
    final controller = CielController(
      settingsStore: SettingsStore(secrets: secrets),
      transport: transport,
      apiFactory: (e, t) => CielApi(e, t, client: backend.client),
      speaker: speaker,
    );
    await controller.init();
    await Future<void>.delayed(Duration.zero);
    return Harness._(controller, transport, backend, speaker, secrets);
  }
}

String frame(String type, Object? data) => jsonEncode({'type': type, 'data': data});

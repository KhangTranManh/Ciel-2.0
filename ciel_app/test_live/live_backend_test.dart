// Live check against a real main_api.py — not part of `flutter test` (lives
// outside test/). Run with the server up:
//   flutter test test_live/live_backend_test.dart --dart-define=SERVER=http://127.0.0.1:8765 --dart-define=TOKEN=...
import 'dart:async';

import 'package:ciel_app/core/api_client.dart';
import 'package:ciel_app/core/ciel_socket.dart';
import 'package:ciel_app/core/endpoints.dart';
import 'package:ciel_app/core/protocol.dart';
import 'package:flutter_test/flutter_test.dart';

const server = String.fromEnvironment('SERVER', defaultValue: 'http://127.0.0.1:8765');
const token = String.fromEnvironment('TOKEN');

void main() {
  final endpoints = CielEndpoints.parse(server)!;

  test('REST: health open, skills need the token', () async {
    final health = await CielApi(endpoints, '').health();
    expect(health.authRequired, isTrue);
    await expectLater(CielApi(endpoints, 'wrong').skills(),
        throwsA(isA<ApiException>().having((e) => e.isUnauthorized, 'unauthorized', true)));
    final skills = await CielApi(endpoints, token).skills();
    expect(skills.ready, isTrue);
    expect(skills.toolCount, greaterThan(0));
    // ignore: avoid_print
    print('skills: ${skills.skills.length} packs, ${skills.toolCount} tools');
  });

  test('WebSocket: wrong token is rejected as unauthorized, not retried', () async {
    final socket = CielSocket();
    final rejected = socket.statusChanges.firstWhere((s) => s == ConnectionStatus.unauthorized);
    socket.connect(endpoints.socketUri('wrong'));
    await rejected.timeout(const Duration(seconds: 10));
    await socket.dispose();
  });

  test('WebSocket: real token → vitals frames → a chat turn gets status and a final reply', () async {
    final socket = CielSocket();
    final opened = socket.statusChanges.firstWhere((s) => s == ConnectionStatus.open);
    final frames = <ServerMessage>[];
    final sub = socket.messages.listen(frames.add);
    socket.connect(endpoints.socketUri(token));
    await opened.timeout(const Duration(seconds: 10));

    await Future<void>.delayed(const Duration(seconds: 5));
    final vitals = frames.whereType<VitalsMessage>();
    expect(vitals, isNotEmpty, reason: 'server sends vitals every ~2-3s');
    expect(frames.whereType<ThoughtMessage>(), isEmpty, reason: 'raw thoughts are off by default');

    expect(socket.send(ClientMessage.chat('ping from the Flutter live check')), isTrue);
    final done = Completer<ServerMessage>();
    final waitSub = socket.messages.listen((m) {
      if ((m is ResponseMessage || m is ErrorMessage) && !done.isCompleted) done.complete(m);
    });
    final reply = await done.future.timeout(const Duration(seconds: 120));
    expect(frames.whereType<StatusMessage>().map((s) => s.text), contains('processing'));
    // ignore: avoid_print
    print('reply type: ${reply.runtimeType}; text: ${switch (reply) {
      ResponseMessage(:final text) => text,
      ErrorMessage(:final text) => text,
      _ => ''
    }}');
    await waitSub.cancel();
    await sub.cancel();
    await socket.dispose();
  }, timeout: const Timeout(Duration(minutes: 3)));
}

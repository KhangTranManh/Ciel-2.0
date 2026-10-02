import 'package:ciel_app/core/ciel_socket.dart';
import 'package:ciel_app/state/chat_turn.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fakes.dart';

void main() {
  test('connects to the configured server with the token, and loads skills with it', () async {
    final h = await Harness.create();
    expect(h.transport.connects.single.toString(), 'wss://ciel.example.com/ws?token=secret-token');
    expect(h.controller.isOpen, isTrue);
    expect(h.controller.skills?.skills.single.module, 'gmail_ops');
    final skillsCall = h.backend.requests.firstWhere((r) => r.url.path == '/skills');
    expect(skillsCall.headers['Authorization'], 'Bearer secret-token');
  });

  test('send → processing → response', () async {
    final h = await Harness.create();
    h.controller.send('  giá vàng hôm nay  ');
    expect(h.transport.sentJson.single, {'message': 'giá vàng hôm nay'});
    expect(h.controller.status, 'processing');
    expect(h.controller.chat.last.role, ChatRole.user);

    h.transport.serverSends(frame('response', 'SJC 13.950'));
    expect(h.controller.busy, isFalse);
    expect(h.controller.chat.last.text, 'SJC 13.950');
    expect(h.controller.chat.last.role, ChatRole.ciel);
  });

  test('sending while offline is reported, never silently dropped', () async {
    final h = await Harness.create();
    h.transport.setStatus(ConnectionStatus.closed);
    h.controller.send('hello');
    expect(h.transport.sent, isEmpty);
    expect(h.controller.chat.last.role, ChatRole.notice);
  });

  test('confirm request → explicit answer is sent once', () async {
    final h = await Harness.create();
    h.transport.serverSends(frame('confirm_request', {
      'tool_name': 'send_gmail_message',
      'preview': 'Send',
      'tool_args': {'to': 'a@b.com'},
    }));
    expect(h.controller.pendingConfirm?.toolName, 'send_gmail_message');
    h.controller.respondConfirm(false);
    h.controller.respondConfirm(true);
    expect(h.transport.sentJson, [
      {'type': 'confirm_response', 'approved': false}
    ]);
    expect(h.controller.pendingConfirm, isNull);
  });

  test('a lost connection drops a pending approval instead of leaving a dead dialog', () async {
    final h = await Harness.create();
    h.controller.send('gửi mail');
    h.transport.serverSends(frame('confirm_request', {'tool_name': 'plan', 'preview': 'x'}));
    h.transport.setStatus(ConnectionStatus.closed);
    expect(h.controller.pendingConfirm, isNull);
    expect(h.controller.busy, isFalse);
    expect(h.controller.chat.last.text, contains('not approved'));
  });

  test('Stop is only sent while a request is running', () async {
    final h = await Harness.create();
    h.controller.cancel();
    expect(h.transport.sent, isEmpty);
    h.controller.send('long task');
    h.controller.cancel();
    expect(h.transport.sentJson.last, {'type': 'cancel'});
    expect(h.controller.status, 'cancelling');
  });

  test('an unauthorized frame is not shown as a chat error', () async {
    final h = await Harness.create();
    h.transport.serverSends(frame('error', 'unauthorized'));
    expect(h.controller.chat, isEmpty);
  });

  test('changing the server reconnects to the new address', () async {
    final h = await Harness.create();
    await h.controller.updateSettings(h.controller.settings.copyWith(serverUrl: '10.0.0.5:8000', token: 'new'));
    expect(h.transport.connects.last.toString(), 'ws://10.0.0.5:8000/ws?token=new');
    expect(h.secrets.values['api_token'], 'new');
  });

  test('read-aloud plays server TTS only when enabled', () async {
    final h = await Harness.create();
    h.transport.serverSends(frame('response', 'không đọc'));
    await Future<void>.delayed(Duration.zero);
    expect(h.speaker.played, isEmpty);

    await h.controller.updateSettings(h.controller.settings.copyWith(speakReplies: true));
    h.transport.serverSends(frame('response', 'đọc cái này'));
    await Future<void>.delayed(const Duration(milliseconds: 10));
    expect(h.speaker.played.single, [1, 2, 3]);
  });

  test('chat history survives a restart', () async {
    final h = await Harness.create();
    h.controller.send('nhớ tin này');
    await Future<void>.delayed(Duration.zero);
    final saved = await const ChatHistoryStore().load();
    expect(saved.single.text, 'nhớ tin này');
  });
}

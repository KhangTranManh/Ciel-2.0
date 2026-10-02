import 'dart:convert';

import 'package:ciel_app/core/endpoints.dart';
import 'package:ciel_app/core/protocol.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('ServerMessage.parse', () {
    test('response, error, status', () {
      expect((ServerMessage.parse('{"type":"response","data":"xin chào"}') as ResponseMessage).text, 'xin chào');
      expect(ServerMessage.parse('{"type":"error","data":"unauthorized"}'),
          isA<ErrorMessage>().having((m) => m.isUnauthorized, 'unauthorized', true));
      expect((ServerMessage.parse('{"type":"status","data":"processing"}') as StatusMessage).text, 'processing');
    });

    test('confirm_request carries tool, preview and args', () {
      final m = ServerMessage.parse(jsonEncode({
        'type': 'confirm_request',
        'data': {'tool_name': 'send_gmail_message', 'preview': 'Send email', 'tool_args': {'to': 'a@b.com'}},
      })) as ConfirmMessage;
      expect(m.request.toolName, 'send_gmail_message');
      expect(m.request.toolArgs['to'], 'a@b.com');
      expect(m.request.isPlan, isFalse);
    });

    test('vitals tolerate missing optional fields from older backends', () {
      final m = ServerMessage.parse('{"type":"vitals","data":{"llm_calls":{"BRAIN":2},"llm_calls_total":2}}')
          as VitalsMessage;
      expect(m.vitals.llmCallsTotal, 2);
      expect(m.vitals.llmTokensTotal, 0);
      expect(m.vitals.skills, isEmpty);
    });

    test('full vitals frame', () {
      final m = ServerMessage.parse(jsonEncode({
        'type': 'vitals',
        'data': {
          'llm_tokens': {'WORKER': {'input': 10, 'output': 5, 'total': 15}},
          'llm_tokens_total': 15,
          'llm_cost_usd_total': 0.0123,
          'skills': [{'module': 'gmail_ops', 'category': 'external', 'tool_count': 9, 'active': true}],
        },
      })) as VitalsMessage;
      expect(m.vitals.llmTokens['WORKER']!.output, 5);
      expect(m.vitals.llmCostUsdTotal, closeTo(0.0123, 1e-9));
      expect(m.vitals.skills.single.active, isTrue);
    });

    test('non-JSON and unknown frames never throw', () {
      expect(ServerMessage.parse('plain text'), isA<ThoughtMessage>());
      expect(ServerMessage.parse('{"type":"future_thing"}'), isA<UnknownMessage>());
      expect(ServerMessage.parse('[1,2]'), isA<ThoughtMessage>());
    });
  });

  test('client frames match main_api.py', () {
    expect(jsonDecode(ClientMessage.chat('hi')), {'message': 'hi'});
    expect(jsonDecode(ClientMessage.confirm(true)), {'type': 'confirm_response', 'approved': true});
    expect(jsonDecode(ClientMessage.cancel()), {'type': 'cancel'});
  });

  group('CielEndpoints.parse', () {
    test('host:port defaults to http/ws', () {
      final e = CielEndpoints.parse('192.168.1.10:8000')!;
      expect(e.httpBase.toString(), 'http://192.168.1.10:8000');
      expect(e.webSocket.toString(), 'ws://192.168.1.10:8000/ws');
      expect(e.isSecure, isFalse);
    });

    test('https becomes wss and keeps a proxy path prefix', () {
      final e = CielEndpoints.parse('https://example.com/ciel/')!;
      expect(e.route('/skills').toString(), 'https://example.com/ciel/skills');
      expect(e.webSocket.toString(), 'wss://example.com/ciel/ws');
    });

    test('a pasted ws URL is accepted', () {
      final e = CielEndpoints.parse('wss://example.com/ws')!;
      expect(e.httpBase.toString(), 'https://example.com');
      expect(e.webSocket.toString(), 'wss://example.com/ws');
    });

    test('token goes in the socket query, not the path', () {
      final e = CielEndpoints.parse('https://example.com')!;
      expect(e.socketUri('abc').toString(), 'wss://example.com/ws?token=abc');
      expect(e.socketUri('').toString(), 'wss://example.com/ws');
    });

    test('rejects empty and non-web schemes', () {
      expect(CielEndpoints.parse(''), isNull);
      expect(CielEndpoints.parse('ftp://example.com'), isNull);
      expect(CielEndpoints.parse('http://'), isNull);
    });
  });
}

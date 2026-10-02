import 'package:ciel_app/app.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fakes.dart';

void main() {
  Future<Harness> pumpApp(WidgetTester tester) async {
    final h = await tester.runAsync(Harness.create);
    await tester.pumpWidget(CielApp(controller: h!.controller));
    await tester.pump();
    return h;
  }

  testWidgets('typing and pressing send delivers the message', (tester) async {
    final h = await pumpApp(tester);
    await tester.enterText(find.byKey(const Key('chat-input')), 'xin chào Ciel');
    await tester.tap(find.byKey(const Key('send-button')));
    await tester.pump();
    expect(h.transport.sentJson.single, {'message': 'xin chào Ciel'});
    expect(find.text('xin chào Ciel'), findsOneWidget);
    expect(find.byKey(const Key('stop-button')), findsOneWidget);
  });

  testWidgets('a reply with a Markdown table renders as a table', (tester) async {
    final h = await pumpApp(tester);
    h.transport.serverSends(frame('response', '| Loại | Mua |\n|---|--:|\n| SJC | 13.950 |'));
    await tester.pump();
    expect(find.byType(Table), findsOneWidget);
    expect(find.textContaining('|---'), findsNothing);
  });

  testWidgets('approval dialog → Approve sends approved=true', (tester) async {
    final h = await pumpApp(tester);
    h.transport.serverSends(frame('confirm_request', {
      'tool_name': 'send_gmail_message',
      'preview': 'Send an email to a@b.com',
      'tool_args': {'to': 'a@b.com'},
    }));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.text('Safety check'), findsOneWidget);
    expect(find.text('send_gmail_message'), findsOneWidget);

    await tester.tap(find.byKey(const Key('confirm-approve')));
    await tester.pump(const Duration(milliseconds: 400));
    expect(h.transport.sentJson.last, {'type': 'confirm_response', 'approved': true});
    expect(find.text('Safety check'), findsNothing);
  });

  testWidgets('no answer within 60s is a deny, never an approval', (tester) async {
    final h = await pumpApp(tester);
    h.transport.serverSends(frame('confirm_request', {'tool_name': 'plan', 'preview': 'Run 2 risky steps'}));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.text('Plan approval'), findsOneWidget);

    for (var i = 0; i < 61; i++) {
      await tester.pump(const Duration(seconds: 1));
    }
    await tester.pump(const Duration(milliseconds: 400));
    expect(h.transport.sentJson.last, {'type': 'confirm_response', 'approved': false});
    expect(find.text('Plan approval'), findsNothing);
  });

  testWidgets('settings screen shows where the app will connect', (tester) async {
    await pumpApp(tester);
    await tester.tap(find.byKey(const Key('open-settings')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('settings-server')), '192.168.1.10:8000');
    await tester.pump();
    expect(find.textContaining('ws://192.168.1.10:8000/ws'), findsOneWidget);
  });
}

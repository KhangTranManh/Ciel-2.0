import 'package:ciel_app/core/settings_store.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';

// The served dashboard is built with CIEL_SAME_ORIGIN=true; the web branch can be run with
//   flutter test --platform chrome --dart-define=CIEL_SAME_ORIGIN=true test/settings_store_test.dart
void main() {
  final page = Uri.parse('https://ciel.example.com/#/settings');

  test('the dashboard served by Ciel connects to the address it was loaded from', () {
    final expected = SettingsStore.sameOrigin && kIsWeb ? 'https://ciel.example.com' : SettingsStore.defaultServerUrl;
    expect(SettingsStore.initialServerUrl(pageUri: page), expected);
  });

  test('a non-web or non-http page never becomes the server address', () {
    expect(SettingsStore.initialServerUrl(pageUri: Uri.parse('file:///C:/app/index.html')),
        SettingsStore.defaultServerUrl);
  });
}

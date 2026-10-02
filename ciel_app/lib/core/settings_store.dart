import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

class CielSettings {
  const CielSettings({
    required this.serverUrl,
    this.token = '',
    this.speakReplies = false,
    this.voiceLocale = 'vi_VN',
  });

  final String serverUrl;
  final String token;
  final bool speakReplies;
  final String voiceLocale;

  CielSettings copyWith({String? serverUrl, String? token, bool? speakReplies, String? voiceLocale}) =>
      CielSettings(
        serverUrl: serverUrl ?? this.serverUrl,
        token: token ?? this.token,
        speakReplies: speakReplies ?? this.speakReplies,
        voiceLocale: voiceLocale ?? this.voiceLocale,
      );
}

/// Where the access token lives. The real app uses the OS keychain/keystore;
/// tests use [MemorySecretStore].
abstract class SecretStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
}

class SecureSecretStore implements SecretStore {
  const SecureSecretStore();
  static const _storage = FlutterSecureStorage();

  @override
  Future<String?> read(String key) => _storage.read(key: key);

  @override
  Future<void> write(String key, String value) => _storage.write(key: key, value: value);
}

class MemorySecretStore implements SecretStore {
  final Map<String, String> values = {};

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> write(String key, String value) async => values[key] = value;
}

class SettingsStore {
  SettingsStore({SecretStore secrets = const SecureSecretStore()}) : _secrets = secrets;

  /// Build-time default: `flutter run --dart-define=CIEL_SERVER_URL=https://ciel.example.com`.
  static const defaultServerUrl =
      String.fromEnvironment('CIEL_SERVER_URL', defaultValue: 'http://localhost:8000');

  static const _serverKey = 'server_url';
  static const _speakKey = 'speak_replies';
  static const _localeKey = 'voice_locale';
  static const _tokenKey = 'api_token';

  final SecretStore _secrets;

  Future<CielSettings> load() async {
    final prefs = await SharedPreferences.getInstance();
    String token = '';
    try {
      token = await _secrets.read(_tokenKey) ?? '';
    } catch (_) {
      // Keychain unavailable (e.g. locked); the app still runs and asks again.
    }
    return CielSettings(
      serverUrl: prefs.getString(_serverKey) ?? defaultServerUrl,
      token: token,
      speakReplies: prefs.getBool(_speakKey) ?? false,
      voiceLocale: prefs.getString(_localeKey) ?? 'vi_VN',
    );
  }

  Future<void> save(CielSettings settings) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_serverKey, settings.serverUrl.trim());
    await prefs.setBool(_speakKey, settings.speakReplies);
    await prefs.setString(_localeKey, settings.voiceLocale);
    await _secrets.write(_tokenKey, settings.token.trim());
  }
}

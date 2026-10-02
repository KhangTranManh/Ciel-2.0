import 'dart:async';

import 'package:flutter/foundation.dart';

import '../core/api_client.dart';
import '../core/ciel_socket.dart';
import '../core/endpoints.dart';
import '../core/protocol.dart';
import '../core/settings_store.dart';
import '../features/voice/speaker.dart';
import 'chat_turn.dart';

typedef ApiFactory = CielApi Function(CielEndpoints endpoints, String token);

/// App state: chat, run status, safety confirmations, usage and skills.
/// Every screen and modality talks to this, never to the socket directly.
class CielController extends ChangeNotifier {
  CielController({
    required SettingsStore settingsStore,
    required CielTransport transport,
    ChatHistoryStore history = const ChatHistoryStore(),
    ApiFactory? apiFactory,
    Speaker? speaker,
  })  : _settingsStore = settingsStore,
        _transport = transport,
        _history = history,
        _apiFactory = apiFactory ?? ((e, t) => CielApi(e, t)),
        _speaker = speaker;

  static const maxChat = 200;

  final SettingsStore _settingsStore;
  final CielTransport _transport;
  final ChatHistoryStore _history;
  final ApiFactory _apiFactory;
  final Speaker? _speaker;
  final List<StreamSubscription<dynamic>> _subscriptions = [];

  CielSettings settings = const CielSettings(serverUrl: SettingsStore.defaultServerUrl);
  CielEndpoints? endpoints;
  CielApi? api;

  List<ChatTurn> chat = [];
  ConnectionStatus connection = ConnectionStatus.idle;
  String status = '';
  ConfirmRequest? pendingConfirm;
  Vitals? vitals;
  SkillsResponse? skills;
  String? skillsError;
  bool speaking = false;
  int _nextId = 1;

  bool get busy => status.isNotEmpty;
  bool get isOpen => connection == ConnectionStatus.open;
  bool get serverConfigured => endpoints != null;

  Future<void> init() async {
    settings = await _settingsStore.load();
    chat = await _history.load();
    _nextId = chat.isEmpty ? 1 : chat.map((t) => t.id).reduce((a, b) => a > b ? a : b) + 1;
    _subscriptions
      ..add(_transport.messages.listen(_onMessage))
      ..add(_transport.statusChanges.listen(_onConnection));
    final speaker = _speaker;
    if (speaker != null) {
      _subscriptions.add(speaker.speaking.listen((value) {
        speaking = value;
        notifyListeners();
      }));
    }
    _applySettings();
    notifyListeners();
  }

  void _applySettings() {
    api?.close();
    api = null;
    skills = null;
    skillsError = null;
    endpoints = CielEndpoints.parse(settings.serverUrl);
    final target = endpoints;
    if (target == null) return;
    api = _apiFactory(target, settings.token);
    _transport.connect(target.socketUri(settings.token));
    unawaited(refreshSkills());
  }

  Future<void> updateSettings(CielSettings next) async {
    final reconnect = next.serverUrl.trim() != settings.serverUrl.trim() || next.token.trim() != settings.token.trim();
    await _settingsStore.save(next);
    settings = next;
    if (reconnect) {
      await _transport.disconnect();
      status = '';
      pendingConfirm = null;
      vitals = null;
      _applySettings();
    }
    notifyListeners();
  }

  Future<void> refreshSkills() async {
    final client = api;
    if (client == null) return;
    try {
      skills = await client.skills();
      skillsError = skills?.error;
    } on ApiException catch (e) {
      skillsError = e.message;
    }
    notifyListeners();
  }

  void reconnect() => _transport.reconnectNow();

  void _onConnection(ConnectionStatus value) {
    connection = value;
    if (value != ConnectionStatus.open) {
      if (pendingConfirm != null) {
        // The server cannot receive an answer any more; it will deny on its own timeout.
        pendingConfirm = null;
        _push(ChatRole.notice, 'Connection lost while an approval was pending — the action was not approved.');
      }
      if (busy) status = '';
    } else {
      unawaited(refreshSkills());
    }
    notifyListeners();
  }

  void _onMessage(ServerMessage message) {
    switch (message) {
      case StatusMessage(:final text):
        status = text;
      case ResponseMessage(:final text):
        status = '';
        _push(ChatRole.ciel, text);
        unawaited(_speak(text));
      case ErrorMessage(:final text, :final isUnauthorized):
        status = '';
        if (!isUnauthorized) _push(ChatRole.error, text);
      case VitalsMessage(:final vitals):
        this.vitals = vitals;
      case ConfirmMessage(:final request):
        pendingConfirm = request;
      case ThoughtMessage():
      case UnknownMessage():
        return;
    }
    notifyListeners();
  }

  void send(String text) {
    final trimmed = text.trim();
    if (trimmed.isEmpty) return;
    if (!isOpen) {
      _push(ChatRole.notice, 'Not connected — message was not sent.');
      notifyListeners();
      return;
    }
    _push(ChatRole.user, trimmed);
    status = 'processing';
    if (!_transport.send(ClientMessage.chat(trimmed))) {
      status = '';
      _push(ChatRole.notice, 'Send failed — the connection closed mid-flight.');
    }
    notifyListeners();
  }

  void respondConfirm(bool approved) {
    if (pendingConfirm == null) return;
    pendingConfirm = null;
    if (!_transport.send(ClientMessage.confirm(approved))) {
      _push(ChatRole.notice, 'Could not deliver your answer — the server will deny the action on timeout.');
    }
    notifyListeners();
  }

  void cancel() {
    if (!busy) return;
    if (_transport.send(ClientMessage.cancel())) {
      status = 'cancelling';
    } else {
      _push(ChatRole.notice, 'Could not send Stop — not connected.');
    }
    notifyListeners();
  }

  void retryLast() {
    final last = chat.lastWhere((t) => t.role == ChatRole.user, orElse: () => ChatTurn(id: 0, role: ChatRole.notice, text: '', at: DateTime.now()));
    if (last.text.isNotEmpty) send(last.text);
  }

  Future<void> clearHistory() async {
    chat = [];
    await _history.clear();
    notifyListeners();
  }

  Future<void> stopSpeaking() async => _speaker?.stop();

  Future<void> _speak(String text) async {
    final client = api;
    final speaker = _speaker;
    if (!settings.speakReplies || client == null || speaker == null) return;
    try {
      final audio = await client.tts(text);
      if (audio != null) await speaker.play(audio);
    } catch (e) {
      _push(ChatRole.notice, 'Read-aloud failed: $e');
      notifyListeners();
    }
  }

  void _push(ChatRole role, String text) {
    chat = [...chat, ChatTurn(id: _nextId++, role: role, text: text, at: DateTime.now())];
    if (chat.length > maxChat) chat = chat.sublist(chat.length - maxChat);
    unawaited(_history.save(chat));
  }

  @override
  void dispose() {
    for (final s in _subscriptions) {
      s.cancel();
    }
    api?.close();
    unawaited(_transport.disconnect());
    super.dispose();
  }
}

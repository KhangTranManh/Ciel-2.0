import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

enum ChatRole { user, ciel, error, notice }

class ChatTurn {
  const ChatTurn({required this.id, required this.role, required this.text, required this.at});

  factory ChatTurn.fromJson(Map<String, dynamic> json) => ChatTurn(
        id: json['id'] is int ? json['id'] as int : 0,
        role: ChatRole.values.firstWhere((r) => r.name == json['role'], orElse: () => ChatRole.notice),
        text: '${json['text'] ?? ''}',
        at: DateTime.tryParse('${json['at']}') ?? DateTime.now(),
      );

  final int id;
  final ChatRole role;
  final String text;
  final DateTime at;

  Map<String, dynamic> toJson() => {'id': id, 'role': role.name, 'text': text, 'at': at.toIso8601String()};
}

/// Keeps the last [limit] turns on the device so a restart does not wipe the chat.
class ChatHistoryStore {
  const ChatHistoryStore({this.limit = 200});

  static const _key = 'chat_history_v1';
  final int limit;

  Future<List<ChatTurn>> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_key);
    if (raw == null) return [];
    try {
      final list = jsonDecode(raw) as List;
      return list.map((e) => ChatTurn.fromJson(Map<String, dynamic>.from(e as Map))).toList();
    } catch (_) {
      return [];
    }
  }

  Future<void> save(List<ChatTurn> turns) async {
    final prefs = await SharedPreferences.getInstance();
    final tail = turns.length > limit ? turns.sublist(turns.length - limit) : turns;
    await prefs.setString(_key, jsonEncode(tail.map((t) => t.toJson()).toList()));
  }

  Future<void> clear() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_key);
  }
}

// Wire protocol shared with the Python backend (main_api.py).
// Mirrors the server's send_json shapes; unknown fields are ignored and missing
// optional fields default, so an older or newer backend still parses.
import 'dart:convert';

int _int(Object? v) => v is num ? v.toInt() : int.tryParse('$v') ?? 0;
double _double(Object? v) => v is num ? v.toDouble() : double.tryParse('$v') ?? 0;
Map<String, dynamic> _map(Object? v) =>
    v is Map ? Map<String, dynamic>.from(v) : const <String, dynamic>{};
List<dynamic> _list(Object? v) => v is List ? v : const [];

class ConfirmRequest {
  const ConfirmRequest({required this.toolName, required this.preview, this.toolArgs = const {}});

  factory ConfirmRequest.fromJson(Map<String, dynamic> json) => ConfirmRequest(
        toolName: '${json['tool_name'] ?? ''}',
        preview: '${json['preview'] ?? ''}',
        toolArgs: _map(json['tool_args']),
      );

  final String toolName;
  final String preview;
  final Map<String, dynamic> toolArgs;

  /// "plan" means one approval covering every risky step before anything runs.
  bool get isPlan => toolName == 'plan';
}

/// Backend safety-gate wait in main_api.py; the dialog countdown mirrors it.
const confirmTimeout = Duration(seconds: 60);

class TokenCount {
  const TokenCount({this.input = 0, this.output = 0, this.total = 0});

  factory TokenCount.fromJson(Map<String, dynamic> json) =>
      TokenCount(input: _int(json['input']), output: _int(json['output']), total: _int(json['total']));

  final int input;
  final int output;
  final int total;
}

class SkillActivity {
  const SkillActivity({required this.module, this.category = '', this.toolCount = 0, this.active = false});

  factory SkillActivity.fromJson(Map<String, dynamic> json) => SkillActivity(
        module: '${json['module'] ?? ''}',
        category: '${json['category'] ?? ''}',
        toolCount: _int(json['tool_count']),
        active: json['active'] == true,
      );

  final String module;
  final String category;
  final int toolCount;
  final bool active;
}

class Vitals {
  const Vitals({
    this.llmCalls = const {},
    this.llmCallsTotal = 0,
    this.llmTokens = const {},
    this.llmTokensTotal = 0,
    this.llmCostUsd = const {},
    this.llmCostUsdTotal = 0,
    this.tiers = const {},
    this.skills = const [],
  });

  factory Vitals.fromJson(Map<String, dynamic> json) => Vitals(
        llmCalls: _map(json['llm_calls']).map((k, v) => MapEntry(k, _int(v))),
        llmCallsTotal: _int(json['llm_calls_total']),
        llmTokens: _map(json['llm_tokens']).map((k, v) => MapEntry(k, TokenCount.fromJson(_map(v)))),
        llmTokensTotal: _int(json['llm_tokens_total']),
        llmCostUsd: _map(json['llm_cost_usd']).map((k, v) => MapEntry(k, _double(v))),
        llmCostUsdTotal: _double(json['llm_cost_usd_total']),
        tiers: _map(json['tiers']).map((k, v) => MapEntry(k, v == true)),
        skills: _list(json['skills']).map((e) => SkillActivity.fromJson(_map(e))).toList(),
      );

  final Map<String, int> llmCalls;
  final int llmCallsTotal;
  final Map<String, TokenCount> llmTokens;
  final int llmTokensTotal;
  final Map<String, double> llmCostUsd;
  final double llmCostUsdTotal;
  final Map<String, bool> tiers;
  final List<SkillActivity> skills;
}

class SkillTool {
  const SkillTool({required this.name, this.description = ''});

  factory SkillTool.fromJson(Map<String, dynamic> json) =>
      SkillTool(name: '${json['name'] ?? ''}', description: '${json['description'] ?? ''}');

  final String name;
  final String description;
}

class SkillModule {
  const SkillModule({required this.module, this.category = '', this.toolCount = 0, this.tools = const []});

  factory SkillModule.fromJson(Map<String, dynamic> json) => SkillModule(
        module: '${json['module'] ?? ''}',
        category: '${json['category'] ?? ''}',
        toolCount: _int(json['tool_count']),
        tools: _list(json['tools']).map((e) => SkillTool.fromJson(_map(e))).toList(),
      );

  final String module;
  final String category;
  final int toolCount;
  final List<SkillTool> tools;
}

class SkillsResponse {
  const SkillsResponse({this.ready = false, this.skills = const [], this.error});

  factory SkillsResponse.fromJson(Map<String, dynamic> json) => SkillsResponse(
        ready: json['ready'] == true,
        skills: _list(json['skills']).map((e) => SkillModule.fromJson(_map(e))).toList(),
        error: json['error'] == null ? null : '${json['error']}',
      );

  final bool ready;
  final List<SkillModule> skills;
  final String? error;

  int get toolCount => skills.fold(0, (sum, s) => sum + s.toolCount);
}

// ---- Server -> client frames ----
sealed class ServerMessage {
  const ServerMessage();

  static ServerMessage parse(String raw) {
    final Object? decoded;
    try {
      decoded = jsonDecode(raw);
    } catch (_) {
      return ThoughtMessage(raw);
    }
    if (decoded is! Map) return ThoughtMessage(raw);
    final json = Map<String, dynamic>.from(decoded);
    final data = json['data'];
    switch (json['type']) {
      case 'response':
        return ResponseMessage('${data ?? ''}');
      case 'error':
        return ErrorMessage('${data ?? ''}');
      case 'status':
        return StatusMessage('${data ?? ''}');
      case 'vitals':
        return VitalsMessage(Vitals.fromJson(_map(data)));
      case 'confirm_request':
        return ConfirmMessage(ConfirmRequest.fromJson(_map(data)));
      case 'thought':
        return ThoughtMessage('${data ?? ''}');
      default:
        return UnknownMessage(raw);
    }
  }
}

class ResponseMessage extends ServerMessage {
  const ResponseMessage(this.text);
  final String text;
}

class ErrorMessage extends ServerMessage {
  const ErrorMessage(this.text);
  final String text;

  /// Sent by the server just before closing a socket with code 4401.
  bool get isUnauthorized => text == 'unauthorized';
}

class StatusMessage extends ServerMessage {
  const StatusMessage(this.text);
  final String text;
}

class VitalsMessage extends ServerMessage {
  const VitalsMessage(this.vitals);
  final Vitals vitals;
}

class ConfirmMessage extends ServerMessage {
  const ConfirmMessage(this.request);
  final ConfirmRequest request;
}

class ThoughtMessage extends ServerMessage {
  const ThoughtMessage(this.text);
  final String text;
}

class UnknownMessage extends ServerMessage {
  const UnknownMessage(this.raw);
  final String raw;
}

// ---- Client -> server frames ----
abstract final class ClientMessage {
  static String chat(String text) => jsonEncode({'message': text});
  static String confirm(bool approved) => jsonEncode({'type': 'confirm_response', 'approved': approved});
  static String cancel() => jsonEncode({'type': 'cancel'});
}

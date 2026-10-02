import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/ciel_socket.dart';
import '../../core/protocol.dart';
import '../../state/ciel_controller.dart';
import '../../theme/ciel_theme.dart';
import '../confirm/confirm_view.dart';
import '../settings/settings_screen.dart';
import '../skills/skills_panel.dart';
import '../vitals/vitals_bar.dart';
import 'connection_banner.dart';
import 'input_bar.dart';
import 'transcript.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key, required this.controller});

  final CielController controller;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  static const _wideBreakpoint = 900.0;
  ConfirmRequest? _shownConfirm;

  CielController get _c => widget.controller;

  @override
  void initState() {
    super.initState();
    _c.addListener(_onControllerChanged);
  }

  @override
  void dispose() {
    _c.removeListener(_onControllerChanged);
    super.dispose();
  }

  void _onControllerChanged() {
    final request = _c.pendingConfirm;
    if (request != null && !identical(request, _shownConfirm)) {
      _shownConfirm = request;
      WidgetsBinding.instance.addPostFrameCallback((_) => _askConfirm(request));
    } else if (request == null && _shownConfirm != null) {
      // Cleared elsewhere (connection lost): close the stale dialog without answering.
      final shown = _shownConfirm;
      _shownConfirm = null;
      if (shown != null && mounted) Navigator.of(context).popUntil((route) => route is! PopupRoute);
    }
  }

  Future<void> _askConfirm(ConfirmRequest request) async {
    if (!mounted) return;
    final approved = await showConfirm(context, request);
    if (identical(_c.pendingConfirm, request)) _c.respondConfirm(approved);
    if (identical(_shownConfirm, request)) _shownConfirm = null;
  }

  void _openSettings() {
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => SettingsScreen(controller: _c)));
  }

  String _sessionLabel() {
    if (_c.speaking) return 'speaking';
    if (_c.busy) return _c.status == 'cancelling' ? 'stopping' : 'running';
    return switch (_c.connection) {
      ConnectionStatus.open => 'ready',
      ConnectionStatus.connecting => 'connecting',
      ConnectionStatus.unauthorized => 'token rejected',
      ConnectionStatus.closed => 'offline',
      ConnectionStatus.idle => 'not configured',
    };
  }

  Color _dotColor(CielColors c) {
    if (_c.busy) return c.accent;
    return switch (_c.connection) {
      ConnectionStatus.open => Colors.greenAccent.shade400,
      ConnectionStatus.connecting => Colors.amber,
      _ => c.danger,
    };
  }

  Widget _skillsPanel() => SkillsPanel(
        skills: _c.skills,
        activity: _c.vitals?.skills ?? const [],
        error: _c.skillsError,
        onRefresh: _c.refreshSkills,
      );

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: _c,
      builder: (context, _) {
        final c = context.ciel;
        final wide = MediaQuery.sizeOf(context).width >= _wideBreakpoint;
        final chatColumn = Column(
          children: [
            ConnectionBanner(
              status: _c.connection,
              serverLabel: _c.endpoints?.display ?? 'server',
              onRetry: _c.reconnect,
              onOpenSettings: _openSettings,
            ),
            Expanded(child: Transcript(chat: _c.chat, status: _c.status, onRetry: _c.retryLast)),
            InputBar(
              enabled: _c.isOpen,
              busy: _c.busy,
              voiceLocale: _c.settings.voiceLocale,
              onSend: _c.send,
              onStop: _c.cancel,
            ),
          ],
        );

        return CallbackShortcuts(
          bindings: {
            const SingleActivator(LogicalKeyboardKey.escape): () {
              if (_c.busy) _c.cancel();
            },
          },
          child: Scaffold(
            drawer: wide ? null : Drawer(child: SafeArea(child: _skillsPanel())),
            appBar: AppBar(
              titleSpacing: wide ? 16 : 0,
              title: Row(
                children: [
                  Container(
                    width: 9,
                    height: 9,
                    decoration: BoxDecoration(color: _dotColor(c), shape: BoxShape.circle),
                  ),
                  const SizedBox(width: 10),
                  const Text('Ciel '),
                  Text('2.0', style: TextStyle(fontStyle: FontStyle.italic, color: c.mutedForeground)),
                  const SizedBox(width: 10),
                  Flexible(
                    child: Text(
                      _sessionLabel(),
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(color: c.mutedForeground),
                    ),
                  ),
                ],
              ),
              actions: [
                if (wide) Padding(padding: const EdgeInsets.only(right: 8), child: VitalsChip(vitals: _c.vitals)),
                if (!wide && _c.vitals != null)
                  IconButton(
                    tooltip: 'Usage',
                    onPressed: () => showModalBottomSheet<void>(
                      context: context,
                      builder: (_) => VitalsDetails(vitals: _c.vitals!),
                    ),
                    icon: const Icon(Icons.query_stats),
                  ),
                IconButton(
                  tooltip: _c.settings.speakReplies ? 'Read-aloud on' : 'Read-aloud off',
                  onPressed: () async {
                    if (_c.speaking) await _c.stopSpeaking();
                    await _c.updateSettings(_c.settings.copyWith(speakReplies: !_c.settings.speakReplies));
                  },
                  icon: Icon(_c.settings.speakReplies ? Icons.volume_up : Icons.volume_off),
                ),
                IconButton(
                  key: const Key('open-settings'),
                  tooltip: 'Settings',
                  onPressed: _openSettings,
                  icon: const Icon(Icons.settings_outlined),
                ),
              ],
            ),
            body: wide
                ? Row(
                    children: [
                      SizedBox(width: 280, child: _skillsPanel()),
                      VerticalDivider(width: 1, color: c.border),
                      Expanded(child: chatColumn),
                    ],
                  )
                : chatColumn,
          ),
        );
      },
    );
  }
}

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../theme/ciel_theme.dart';
import '../voice/voice_input_button.dart';

/// Enter sends, Shift+Enter inserts a newline (desktop/web); phones use the send button.
class InputBar extends StatefulWidget {
  const InputBar({
    super.key,
    required this.enabled,
    required this.busy,
    required this.voiceLocale,
    required this.onSend,
    required this.onStop,
  });

  final bool enabled;
  final bool busy;
  final String voiceLocale;
  final ValueChanged<String> onSend;
  final VoidCallback onStop;

  @override
  State<InputBar> createState() => _InputBarState();
}

class _InputBarState extends State<InputBar> {
  final _controller = TextEditingController();
  late final FocusNode _focus = FocusNode(onKeyEvent: _onKey);

  KeyEventResult _onKey(FocusNode node, KeyEvent event) {
    if (event is KeyDownEvent &&
        event.logicalKey == LogicalKeyboardKey.enter &&
        !HardwareKeyboard.instance.isShiftPressed) {
      _submit();
      return KeyEventResult.handled;
    }
    return KeyEventResult.ignored;
  }

  void _submit() {
    final text = _controller.text.trim();
    if (text.isEmpty || !widget.enabled) return;
    widget.onSend(text);
    _controller.clear();
    _focus.requestFocus();
  }

  @override
  void dispose() {
    _controller.dispose();
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.ciel;
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(12, 8, 8, 10),
        decoration: BoxDecoration(border: Border(top: BorderSide(color: c.border))),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Expanded(
              child: TextField(
                key: const Key('chat-input'),
                controller: _controller,
                focusNode: _focus,
                enabled: widget.enabled,
                minLines: 1,
                maxLines: 6,
                textInputAction: TextInputAction.newline,
                decoration: InputDecoration(
                  hintText: widget.enabled ? 'Message Ciel' : 'Not connected',
                ),
              ),
            ),
            const SizedBox(width: 4),
            if (widget.busy)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: FilledButton.tonalIcon(
                  key: const Key('stop-button'),
                  onPressed: widget.onStop,
                  icon: const Icon(Icons.stop_rounded),
                  label: const Text('Stop'),
                ),
              )
            else ...[
              VoiceInputButton(
                enabled: widget.enabled,
                localeId: widget.voiceLocale,
                onResult: widget.onSend,
              ),
              IconButton.filled(
                key: const Key('send-button'),
                tooltip: 'Send',
                onPressed: widget.enabled ? _submit : null,
                icon: const Icon(Icons.arrow_upward_rounded),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

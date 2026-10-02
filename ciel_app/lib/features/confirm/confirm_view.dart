import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/protocol.dart';
import '../../theme/ciel_theme.dart';

/// Shows the safety-gate request and resolves to the Master's answer.
/// Never dismissible by tapping outside: silence must not become consent, so the
/// only outcomes are an explicit answer or the auto-deny at 0s.
Future<bool> showConfirm(BuildContext context, ConfirmRequest request, {Duration timeout = confirmTimeout}) async {
  HapticFeedback.heavyImpact();
  final Future<bool?> answer;
  if (MediaQuery.sizeOf(context).width >= 700) {
    answer = showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (_) => Dialog(
        insetPadding: const EdgeInsets.all(24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 640, maxHeight: 640),
          child: ConfirmView(request: request, timeout: timeout),
        ),
      ),
    );
  } else {
    answer = showModalBottomSheet<bool>(
      context: context,
      isDismissible: false,
      enableDrag: false,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => FractionallySizedBox(
        heightFactor: 0.9,
        child: ConfirmView(request: request, timeout: timeout),
      ),
    );
  }
  return await answer ?? false;
}

class ConfirmView extends StatefulWidget {
  const ConfirmView({super.key, required this.request, this.timeout = confirmTimeout});

  final ConfirmRequest request;
  final Duration timeout;

  @override
  State<ConfirmView> createState() => _ConfirmViewState();
}

class _ConfirmViewState extends State<ConfirmView> {
  late int _left = widget.timeout.inSeconds;
  Timer? _timer;
  bool _answered = false;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      setState(() => _left--);
      if (_left <= 0) _answer(false);
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  void _answer(bool approved) {
    if (_answered) return;
    _answered = true;
    _timer?.cancel();
    Navigator.of(context).pop(approved);
  }

  KeyEventResult _onKey(FocusNode node, KeyEvent event) {
    if (event is! KeyDownEvent) return KeyEventResult.ignored;
    final key = event.logicalKey;
    if (key == LogicalKeyboardKey.escape || key == LogicalKeyboardKey.keyN) {
      _answer(false);
      return KeyEventResult.handled;
    }
    if (key == LogicalKeyboardKey.enter || key == LogicalKeyboardKey.keyY) {
      _answer(true);
      return KeyEventResult.handled;
    }
    return KeyEventResult.ignored;
  }

  @override
  Widget build(BuildContext context) {
    final c = context.ciel;
    final theme = Theme.of(context);
    final request = widget.request;
    final urgent = _left <= 10;
    final mono = theme.textTheme.bodySmall?.copyWith(fontFamily: 'monospace', height: 1.4);

    return Focus(
      autofocus: true,
      onKeyEvent: _onKey,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 18, 20, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Icon(Icons.warning_amber_rounded, color: c.danger),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    request.isPlan ? 'Plan approval' : 'Safety check',
                    style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600),
                  ),
                ),
                Container(
                  key: const Key('confirm-timer'),
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: urgent ? c.danger.withValues(alpha: 0.15) : c.muted,
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Text('${_left}s', style: TextStyle(color: urgent ? c.danger : c.mutedForeground)),
                ),
              ],
            ),
            if (!request.isPlan) ...[
              const SizedBox(height: 6),
              Text(request.toolName, style: mono?.copyWith(color: c.accent)),
            ],
            const SizedBox(height: 12),
            Expanded(
              child: Container(
                decoration: BoxDecoration(
                  color: theme.scaffoldBackgroundColor,
                  border: Border.all(color: c.border),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: SingleChildScrollView(
                  padding: const EdgeInsets.all(12),
                  child: SelectableText.rich(
                    TextSpan(children: [
                      TextSpan(text: request.preview, style: mono),
                      if (request.toolArgs.isNotEmpty)
                        TextSpan(
                          text: '\n\n${const JsonEncoder.withIndent('  ').convert(request.toolArgs)}',
                          style: mono?.copyWith(color: c.mutedForeground),
                        ),
                    ]),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 10),
            Text(
              'Y / Enter approve · N / Esc deny · denied automatically at 0s',
              style: theme.textTheme.bodySmall?.copyWith(color: c.mutedForeground),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    key: const Key('confirm-deny'),
                    onPressed: () => _answer(false),
                    style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(48)),
                    child: const Text('Deny'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: FilledButton(
                    key: const Key('confirm-approve'),
                    onPressed: () => _answer(true),
                    style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)),
                    child: const Text('Approve'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

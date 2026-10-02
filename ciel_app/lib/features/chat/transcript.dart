import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';

import '../../state/chat_turn.dart';
import '../../theme/ciel_theme.dart';

class Transcript extends StatefulWidget {
  const Transcript({super.key, required this.chat, required this.status, required this.onRetry});

  final List<ChatTurn> chat;
  final String status;
  final VoidCallback onRetry;

  @override
  State<Transcript> createState() => _TranscriptState();
}

class _TranscriptState extends State<Transcript> {
  final _scroll = ScrollController();

  @override
  void didUpdateWidget(Transcript old) {
    super.didUpdateWidget(old);
    if (widget.chat.length != old.chat.length || widget.status != old.status) _scrollToEnd();
  }

  @override
  void initState() {
    super.initState();
    _scrollToEnd();
  }

  void _scrollToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.ciel;
    final chat = widget.chat;
    if (chat.isEmpty && widget.status.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Text(
            'Ask Ciel anything — check mail, prices, plans, files…',
            textAlign: TextAlign.center,
            style: TextStyle(color: c.mutedForeground),
          ),
        ),
      );
    }
    final lastIsError = chat.isNotEmpty && chat.last.role == ChatRole.error;
    final extra = widget.status.isNotEmpty ? 1 : 0;
    return SelectionArea(
      child: ListView.builder(
        controller: _scroll,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        itemCount: chat.length + extra,
        itemBuilder: (context, index) {
          if (index == chat.length) return _RunningRow(status: widget.status);
          final turn = chat[index];
          final isLast = index == chat.length - 1;
          return _MessageRow(
            key: ValueKey(turn.id),
            turn: turn,
            onRetry: isLast && lastIsError && widget.status.isEmpty ? widget.onRetry : null,
          );
        },
      ),
    );
  }
}

class _MessageRow extends StatelessWidget {
  const _MessageRow({super.key, required this.turn, this.onRetry});

  final ChatTurn turn;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final c = context.ciel;
    final theme = Theme.of(context);
    final maxWidth = MediaQuery.sizeOf(context).width * 0.86;

    switch (turn.role) {
      case ChatRole.user:
        return Align(
          alignment: Alignment.centerRight,
          child: Container(
            constraints: BoxConstraints(maxWidth: maxWidth.clamp(0, 640)),
            margin: const EdgeInsets.symmetric(vertical: 6),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: BoxDecoration(color: c.userBubble, borderRadius: BorderRadius.circular(16)),
            child: Text(turn.text, style: TextStyle(color: c.onUserBubble)),
          ),
        );
      case ChatRole.ciel:
        return Align(
          alignment: Alignment.centerLeft,
          child: Container(
            constraints: BoxConstraints(maxWidth: maxWidth.clamp(0, 760)),
            margin: const EdgeInsets.symmetric(vertical: 6),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: BoxDecoration(
              color: c.card,
              border: Border.all(color: c.border),
              borderRadius: BorderRadius.circular(16),
            ),
            child: MarkdownBody(
              data: turn.text,
              softLineBreak: true,
              styleSheet: MarkdownStyleSheet.fromTheme(theme).copyWith(
                tableBorder: TableBorder.all(color: c.border),
                tableCellsPadding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                code: theme.textTheme.bodySmall?.copyWith(fontFamily: 'monospace', backgroundColor: c.muted),
                codeblockDecoration: BoxDecoration(color: c.muted, borderRadius: BorderRadius.circular(8)),
                blockquoteDecoration: BoxDecoration(
                  border: Border(left: BorderSide(color: c.accent, width: 3)),
                ),
              ),
            ),
          ),
        );
      case ChatRole.error:
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(Icons.error_outline, size: 18, color: c.danger),
              const SizedBox(width: 8),
              Expanded(child: Text(turn.text, style: TextStyle(color: c.danger))),
              if (onRetry != null) TextButton(onPressed: onRetry, child: const Text('Retry')),
            ],
          ),
        );
      case ChatRole.notice:
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Text(
            turn.text,
            textAlign: TextAlign.center,
            style: theme.textTheme.bodySmall?.copyWith(color: c.mutedForeground, fontStyle: FontStyle.italic),
          ),
        );
    }
  }
}

class _RunningRow extends StatelessWidget {
  const _RunningRow({required this.status});
  final String status;

  @override
  Widget build(BuildContext context) {
    final c = context.ciel;
    final label = status == 'cancelling' ? 'stopping at the next step…' : 'running…';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Row(
        children: [
          SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: c.accent)),
          const SizedBox(width: 10),
          Text(label, style: TextStyle(color: c.mutedForeground)),
        ],
      ),
    );
  }
}

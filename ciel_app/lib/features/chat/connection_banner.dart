import 'package:flutter/material.dart';

import '../../core/ciel_socket.dart';
import '../../theme/ciel_theme.dart';

class ConnectionBanner extends StatelessWidget {
  const ConnectionBanner({
    super.key,
    required this.status,
    required this.serverLabel,
    required this.onRetry,
    required this.onOpenSettings,
  });

  final ConnectionStatus status;
  final String serverLabel;
  final VoidCallback onRetry;
  final VoidCallback onOpenSettings;

  @override
  Widget build(BuildContext context) {
    final c = context.ciel;
    final (String text, bool showRetry, bool showSettings) = switch (status) {
      ConnectionStatus.open || ConnectionStatus.idle => ('', false, false),
      ConnectionStatus.connecting => ('Connecting to $serverLabel…', false, false),
      ConnectionStatus.closed => ('Cannot reach $serverLabel — retrying every 2s.', true, true),
      ConnectionStatus.unauthorized => ('The server rejected the access token.', false, true),
    };
    if (text.isEmpty) return const SizedBox.shrink();
    final bad = status == ConnectionStatus.unauthorized || status == ConnectionStatus.closed;
    return Material(
      color: bad ? c.danger.withValues(alpha: 0.12) : c.muted,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        child: Row(
          children: [
            Icon(bad ? Icons.cloud_off_rounded : Icons.cloud_sync_rounded, size: 18, color: bad ? c.danger : c.mutedForeground),
            const SizedBox(width: 10),
            Expanded(child: Text(text, style: Theme.of(context).textTheme.bodySmall)),
            if (showRetry) TextButton(onPressed: onRetry, child: const Text('Retry now')),
            if (showSettings) TextButton(onPressed: onOpenSettings, child: const Text('Settings')),
          ],
        ),
      ),
    );
  }
}

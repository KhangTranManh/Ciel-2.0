import 'package:flutter/material.dart';

import '../../core/protocol.dart';
import '../../theme/ciel_theme.dart';

String formatTokens(int n) {
  if (n >= 1000000) return '${(n / 1000000).toStringAsFixed(1)}M';
  if (n >= 1000) return '${(n / 1000).toStringAsFixed(1)}k';
  return '$n';
}

String formatCost(double usd) => usd < 0.01 && usd > 0 ? '<\$0.01' : '\$${usd.toStringAsFixed(2)}';

/// Session usage: model calls, tokens and estimated cost, from `vitals` frames.
class VitalsChip extends StatelessWidget {
  const VitalsChip({super.key, required this.vitals});

  final Vitals? vitals;

  @override
  Widget build(BuildContext context) {
    final v = vitals;
    final c = context.ciel;
    if (v == null) return const SizedBox.shrink();
    return Tooltip(
      message: 'Session usage — tap for details',
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: () => showModalBottomSheet<void>(context: context, builder: (_) => VitalsDetails(vitals: v)),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          decoration: BoxDecoration(border: Border.all(color: c.border), borderRadius: BorderRadius.circular(16)),
          child: Text(
            '${v.llmCallsTotal} calls · ${formatTokens(v.llmTokensTotal)} tok · ${formatCost(v.llmCostUsdTotal)}',
            style: Theme.of(context).textTheme.labelSmall?.copyWith(color: c.mutedForeground),
          ),
        ),
      ),
    );
  }
}

class VitalsDetails extends StatelessWidget {
  const VitalsDetails({super.key, required this.vitals});

  final Vitals vitals;

  @override
  Widget build(BuildContext context) {
    final c = context.ciel;
    final theme = Theme.of(context);
    final roles = {...vitals.llmCalls.keys, ...vitals.llmTokens.keys}.toList()..sort();
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Session usage', style: theme.textTheme.titleMedium),
            const SizedBox(height: 12),
            for (final role in roles)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Row(
                  children: [
                    SizedBox(width: 110, child: Text(role, style: TextStyle(color: c.mutedForeground))),
                    Expanded(
                      child: Text(
                        '${vitals.llmCalls[role] ?? 0} calls · '
                        '${formatTokens(vitals.llmTokens[role]?.input ?? 0)} in / '
                        '${formatTokens(vitals.llmTokens[role]?.output ?? 0)} out',
                      ),
                    ),
                    Text(formatCost(vitals.llmCostUsd[role] ?? 0)),
                  ],
                ),
              ),
            const Divider(height: 24),
            Row(
              children: [
                const Expanded(child: Text('Total')),
                Text('${formatTokens(vitals.llmTokensTotal)} tokens · ${formatCost(vitals.llmCostUsdTotal)}'),
              ],
            ),
            if (vitals.tiers.isNotEmpty) ...[
              const SizedBox(height: 16),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final tier in vitals.tiers.entries)
                    Chip(
                      label: Text(tier.key),
                      avatar: Icon(tier.value ? Icons.check_circle : Icons.circle_outlined, size: 16,
                          color: tier.value ? c.accent : c.mutedForeground),
                    ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

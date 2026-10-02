import 'package:flutter/material.dart';

import '../../core/protocol.dart';
import '../../theme/ciel_theme.dart';

/// Skill list comes from GET /skills — never hardcoded, so a new backend tool pack
/// appears here without an app update. Recently used modules are highlighted from
/// the `vitals.skills` activity feed.
class SkillsPanel extends StatelessWidget {
  const SkillsPanel({super.key, required this.skills, required this.activity, this.error, required this.onRefresh});

  final SkillsResponse? skills;
  final List<SkillActivity> activity;
  final String? error;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final c = context.ciel;
    final theme = Theme.of(context);
    final active = {for (final a in activity) if (a.active) a.module};
    final modules = [...?skills?.skills]..sort((a, b) {
        final byCategory = a.category.compareTo(b.category);
        return byCategory != 0 ? byCategory : a.module.compareTo(b.module);
      });

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 8, 6),
          child: Row(
            children: [
              Text('Skills', style: theme.textTheme.titleSmall),
              const SizedBox(width: 8),
              if (skills != null)
                Text('${modules.length} packs · ${skills!.toolCount} tools',
                    style: theme.textTheme.bodySmall?.copyWith(color: c.mutedForeground)),
              const Spacer(),
              IconButton(tooltip: 'Reload skills', onPressed: onRefresh, icon: const Icon(Icons.refresh, size: 18)),
            ],
          ),
        ),
        if (error != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            child: Text(error!, style: TextStyle(color: c.danger, fontSize: 12)),
          ),
        Expanded(
          child: modules.isEmpty
              ? Center(
                  child: Text(skills == null ? 'Loading…' : 'No skills loaded',
                      style: TextStyle(color: c.mutedForeground)),
                )
              : ListView(
                  padding: const EdgeInsets.only(bottom: 16),
                  children: [
                    for (final m in modules)
                      ExpansionTile(
                        dense: true,
                        shape: const Border(),
                        leading: Icon(
                          Icons.circle,
                          size: 10,
                          color: active.contains(m.module) ? c.accent : c.border,
                        ),
                        title: Text(m.module.replaceAll('_ops', '')),
                        subtitle: Text('${m.category} · ${m.toolCount} tools',
                            style: TextStyle(color: c.mutedForeground, fontSize: 12)),
                        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                        children: [
                          for (final t in m.tools)
                            Padding(
                              padding: const EdgeInsets.symmetric(vertical: 4),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(t.name, style: const TextStyle(fontFamily: 'monospace', fontSize: 12)),
                                  if (t.description.isNotEmpty)
                                    Text(t.description,
                                        maxLines: 3,
                                        overflow: TextOverflow.ellipsis,
                                        style: TextStyle(color: c.mutedForeground, fontSize: 12)),
                                ],
                              ),
                            ),
                        ],
                      ),
                  ],
                ),
        ),
      ],
    );
  }
}

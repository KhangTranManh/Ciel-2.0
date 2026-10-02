import 'package:flutter/material.dart';

import '../../core/api_client.dart';
import '../../core/endpoints.dart';
import '../../state/ciel_controller.dart';
import '../../theme/ciel_theme.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.controller});

  final CielController controller;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final _server = TextEditingController(text: widget.controller.settings.serverUrl);
  late final _token = TextEditingController(text: widget.controller.settings.token);
  late bool _speak = widget.controller.settings.speakReplies;
  late String _locale = widget.controller.settings.voiceLocale;
  bool _showToken = false;
  bool _testing = false;
  String? _testResult;
  bool _testOk = false;

  static const _locales = {'vi_VN': 'Tiếng Việt', 'en_US': 'English (US)'};

  CielEndpoints? get _endpoints => CielEndpoints.parse(_server.text);

  Future<void> _test() async {
    final endpoints = _endpoints;
    if (endpoints == null) {
      _showInvalidAddress();
      return;
    }
    setState(() {
      _testing = true;
      _testResult = null;
    });
    final api = CielApi(endpoints, _token.text.trim());
    try {
      final health = await api.health();
      await api.skills();
      final security = endpoints.isSecure ? 'HTTPS' : 'unencrypted HTTP — use HTTPS outside your home network';
      final auth = health.authRequired ? 'token accepted' : 'server does not require a token';
      _testOk = true;
      _testResult = 'Connected · ${health.ready ? 'Ciel ready' : 'Ciel still starting'} · $auth · $security';
    } on ApiException catch (e) {
      _testOk = false;
      _testResult = e.message;
    } finally {
      api.close();
    }
    if (mounted) setState(() => _testing = false);
  }

  void _showInvalidAddress() => setState(() {
        _testOk = false;
        _testResult = 'Enter a valid server address.';
      });

  Future<void> _save() async {
    if (_endpoints == null) {
      _showInvalidAddress();
      return;
    }
    await widget.controller.updateSettings(widget.controller.settings.copyWith(
      serverUrl: _server.text.trim(),
      token: _token.text.trim(),
      speakReplies: _speak,
      voiceLocale: _locale,
    ));
    if (mounted) Navigator.of(context).pop();
  }

  Future<void> _clearHistory() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Clear chat history?'),
        content: const Text('This only clears the copy on this device. Ciel\'s own memory is unchanged.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Clear')),
        ],
      ),
    );
    if (ok == true) await widget.controller.clearHistory();
  }

  @override
  void dispose() {
    _server.dispose();
    _token.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.ciel;
    final theme = Theme.of(context);
    final endpoints = _endpoints;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
        actions: [
          TextButton(key: const Key('settings-save'), onPressed: _save, child: const Text('Save')),
        ],
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 640),
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              Text('Server', style: theme.textTheme.titleSmall),
              const SizedBox(height: 8),
              TextField(
                key: const Key('settings-server'),
                controller: _server,
                keyboardType: TextInputType.url,
                autocorrect: false,
                decoration: const InputDecoration(
                  labelText: 'Server address',
                  hintText: 'https://ciel.example.com  or  192.168.1.10:8000',
                ),
                onChanged: (_) => setState(() => _testResult = null),
              ),
              const SizedBox(height: 6),
              Text(
                endpoints == null ? 'Not a valid address' : 'API ${endpoints.display}  ·  socket ${endpoints.webSocket}',
                style: theme.textTheme.bodySmall?.copyWith(color: endpoints == null ? c.danger : c.mutedForeground),
              ),
              const SizedBox(height: 16),
              TextField(
                key: const Key('settings-token'),
                controller: _token,
                obscureText: !_showToken,
                autocorrect: false,
                enableSuggestions: false,
                decoration: InputDecoration(
                  labelText: 'Access token (CIEL_API_TOKEN)',
                  helperText: 'Stored in this device\'s secure keychain. Leave empty for a local server without a token.',
                  suffixIcon: IconButton(
                    tooltip: _showToken ? 'Hide' : 'Show',
                    onPressed: () => setState(() => _showToken = !_showToken),
                    icon: Icon(_showToken ? Icons.visibility_off : Icons.visibility),
                  ),
                ),
                onChanged: (_) => setState(() => _testResult = null),
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  OutlinedButton.icon(
                    key: const Key('settings-test'),
                    onPressed: _testing ? null : _test,
                    icon: _testing
                        ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.network_check),
                    label: const Text('Test connection'),
                  ),
                ],
              ),
              if (_testResult != null) ...[
                const SizedBox(height: 8),
                Text(_testResult!, style: TextStyle(color: _testOk ? c.accent : c.danger)),
              ],
              const Divider(height: 40),
              Text('Voice', style: theme.textTheme.titleSmall),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Read replies aloud'),
                subtitle: const Text('Uses the server\'s voice (POST /tts)'),
                value: _speak,
                onChanged: (v) => setState(() => _speak = v),
              ),
              DropdownButtonFormField<String>(
                initialValue: _locales.containsKey(_locale) ? _locale : 'vi_VN',
                decoration: const InputDecoration(labelText: 'Speech input language'),
                items: [
                  for (final e in _locales.entries) DropdownMenuItem(value: e.key, child: Text(e.value)),
                ],
                onChanged: (v) => setState(() => _locale = v ?? _locale),
              ),
              const Divider(height: 40),
              Text('Data', style: theme.textTheme.titleSmall),
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.delete_outline),
                title: const Text('Clear chat history on this device'),
                onTap: _clearHistory,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

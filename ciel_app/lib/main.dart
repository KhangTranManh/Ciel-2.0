import 'package:flutter/material.dart';

import 'app.dart';
import 'core/ciel_socket.dart';
import 'core/settings_store.dart';
import 'features/voice/speaker.dart';
import 'state/ciel_controller.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final controller = CielController(
    settingsStore: SettingsStore(),
    transport: CielSocket(),
    speaker: JustAudioSpeaker(),
  );
  await controller.init();
  runApp(CielApp(controller: controller));
}

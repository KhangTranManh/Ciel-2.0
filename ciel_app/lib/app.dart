import 'package:flutter/material.dart';

import 'features/chat/chat_screen.dart';
import 'state/ciel_controller.dart';
import 'theme/ciel_theme.dart';

class CielApp extends StatelessWidget {
  const CielApp({super.key, required this.controller});

  final CielController controller;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Ciel',
      debugShowCheckedModeBanner: false,
      theme: buildTheme(Brightness.light),
      darkTheme: buildTheme(Brightness.dark),
      themeMode: ThemeMode.system,
      home: _Lifecycle(controller: controller, child: ChatScreen(controller: controller)),
    );
  }
}

/// Phones suspend sockets in the background; reconnect as soon as the app returns.
class _Lifecycle extends StatefulWidget {
  const _Lifecycle({required this.controller, required this.child});

  final CielController controller;
  final Widget child;

  @override
  State<_Lifecycle> createState() => _LifecycleState();
}

class _LifecycleState extends State<_Lifecycle> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) widget.controller.reconnect();
  }

  @override
  Widget build(BuildContext context) => widget.child;
}

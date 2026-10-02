import 'package:flutter/material.dart';

/// Monochrome tokens from ui/styles.md, with a light counterpart.
class CielColors extends ThemeExtension<CielColors> {
  const CielColors({
    required this.card,
    required this.muted,
    required this.mutedForeground,
    required this.border,
    required this.accent,
    required this.danger,
    required this.userBubble,
    required this.onUserBubble,
  });

  final Color card;
  final Color muted;
  final Color mutedForeground;
  final Color border;
  final Color accent;
  final Color danger;
  final Color userBubble;
  final Color onUserBubble;

  static const dark = CielColors(
    card: Color(0xFF0D0D0D),
    muted: Color(0xFF262626),
    mutedForeground: Color(0xFFA6A6A6),
    border: Color(0xFF333333),
    accent: Color(0xFF62847E),
    danger: Color(0xFFE5484D),
    userBubble: Color(0xFFFFFFFF),
    onUserBubble: Color(0xFF000000),
  );

  static const light = CielColors(
    card: Color(0xFFF5F5F5),
    muted: Color(0xFFE8E8E8),
    mutedForeground: Color(0xFF666666),
    border: Color(0xFFD6D6D6),
    accent: Color(0xFF4A6B65),
    danger: Color(0xFFC62828),
    userBubble: Color(0xFF111111),
    onUserBubble: Color(0xFFFFFFFF),
  );

  @override
  CielColors copyWith({
    Color? card,
    Color? muted,
    Color? mutedForeground,
    Color? border,
    Color? accent,
    Color? danger,
    Color? userBubble,
    Color? onUserBubble,
  }) =>
      CielColors(
        card: card ?? this.card,
        muted: muted ?? this.muted,
        mutedForeground: mutedForeground ?? this.mutedForeground,
        border: border ?? this.border,
        accent: accent ?? this.accent,
        danger: danger ?? this.danger,
        userBubble: userBubble ?? this.userBubble,
        onUserBubble: onUserBubble ?? this.onUserBubble,
      );

  @override
  CielColors lerp(CielColors? other, double t) {
    if (other == null) return this;
    return CielColors(
      card: Color.lerp(card, other.card, t)!,
      muted: Color.lerp(muted, other.muted, t)!,
      mutedForeground: Color.lerp(mutedForeground, other.mutedForeground, t)!,
      border: Color.lerp(border, other.border, t)!,
      accent: Color.lerp(accent, other.accent, t)!,
      danger: Color.lerp(danger, other.danger, t)!,
      userBubble: Color.lerp(userBubble, other.userBubble, t)!,
      onUserBubble: Color.lerp(onUserBubble, other.onUserBubble, t)!,
    );
  }
}

extension CielThemeContext on BuildContext {
  CielColors get ciel => Theme.of(this).extension<CielColors>()!;
}

ThemeData buildTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final tokens = dark ? CielColors.dark : CielColors.light;
  final background = dark ? Colors.black : Colors.white;
  final foreground = dark ? Colors.white : Colors.black;
  final scheme = ColorScheme(
    brightness: brightness,
    primary: foreground,
    onPrimary: background,
    secondary: tokens.accent,
    onSecondary: Colors.white,
    error: tokens.danger,
    onError: Colors.white,
    surface: background,
    onSurface: foreground,
    surfaceContainerHighest: tokens.muted,
    outline: tokens.border,
  );
  final border = OutlineInputBorder(
    borderRadius: BorderRadius.circular(14),
    borderSide: BorderSide(color: tokens.border),
  );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: background,
    extensions: [tokens],
    appBarTheme: AppBarTheme(
      backgroundColor: background,
      foregroundColor: foreground,
      elevation: 0,
      scrolledUnderElevation: 0,
      surfaceTintColor: Colors.transparent,
    ),
    dividerTheme: DividerThemeData(color: tokens.border, space: 1, thickness: 1),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: tokens.card,
      border: border,
      enabledBorder: border,
      focusedBorder: border.copyWith(borderSide: BorderSide(color: tokens.mutedForeground)),
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
    ),
    dialogTheme: DialogThemeData(backgroundColor: tokens.card, surfaceTintColor: Colors.transparent),
    bottomSheetTheme: BottomSheetThemeData(backgroundColor: tokens.card, surfaceTintColor: Colors.transparent),
    drawerTheme: DrawerThemeData(backgroundColor: tokens.card, surfaceTintColor: Colors.transparent),
  );
}

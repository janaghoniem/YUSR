import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class AuraTheme {
  // ─── Pink palette ─────────────────────────────────────────────────────────
  static const Color pink50  = Color(0xFFFFF0F8);
  static const Color pink100 = Color(0xFFFFD6EF);
  static const Color pink200 = Color(0xFFFFAEDD);
  static const Color pink300 = Color(0xFFFF80C0);
  static const Color pink400 = Color(0xFFFF3D9A);
  static const Color pink500 = Color(0xFFE8007F);
  static const Color pink600 = Color(0xFFC4006B);
  static const Color pink700 = Color(0xFF9A0054);
  static const Color pink800 = Color(0xFF700040);
  static const Color pink900 = Color(0xFF47002A);

  // ─── Background scale ─────────────────────────────────────────────────────
  static const Color bgBase     = Color(0xFF0A0908);
  static const Color bgSurface  = Color(0xFF141110);
  static const Color bgElevated = Color(0xFF1E1816);
  static const Color bgOverlay  = Color(0xFF272018);
  static const Color bgMuted    = Color(0xFF363028);

  // ─── Text scale ───────────────────────────────────────────────────────────
  static const Color textPrimary   = Color(0xFFF6F1EC);
  static const Color textSecondary = Color(0xFFBEB2A7);
  static const Color textMuted     = Color(0xFF887B70);
  static const Color textDisabled  = Color(0xFF564E46);

  // ─── Semantic ─────────────────────────────────────────────────────────────
  static const Color error   = Color(0xFFFF6B6B);
  static const Color success = Color(0xFF4DD68C);
  static const Color warning = Color(0xFFFFBA44);

  // Semi-transparent accent used in controls
  static const Color pink400Dull = Color(0x7FFF3D9A);

  // ─── Theme ────────────────────────────────────────────────────────────────
  static ThemeData get darkTheme {
    // Disable network font fetching entirely.
    // This prevents the "Unable to load AssetManifest.json" crash on emulators
    // and devices without internet access. The font must be bundled locally
    // in pubspec.yaml under flutter > fonts.
    GoogleFonts.config.allowRuntimeFetching = false;

    // Base text style using the locally-bundled font family.
    // Falls back to the device sans-serif if the font asset is missing.
    const String fontFamily = 'PlusJakartaSans';

    TextStyle t(
      Color color, {
      FontWeight weight = FontWeight.w400,
      double? size,
      double spacing = 0,
    }) =>
        TextStyle(
          fontFamily: fontFamily,
          color: color,
          fontWeight: weight,
          fontSize: size,
          letterSpacing: spacing,
        );

    return ThemeData(
      brightness: Brightness.dark,
      primaryColor: pink400,
      scaffoldBackgroundColor: bgBase,
      canvasColor: bgSurface,
      cardColor: bgElevated,
      fontFamily: fontFamily,
      textTheme: ThemeData.dark().textTheme.copyWith(
        displayLarge:   t(textPrimary, weight: FontWeight.w600),
        displayMedium:  t(textPrimary, weight: FontWeight.w600),
        displaySmall:   t(textPrimary, weight: FontWeight.w600),
        headlineLarge:  t(textPrimary, weight: FontWeight.w600),
        headlineMedium: t(textPrimary, weight: FontWeight.w600),
        headlineSmall:  t(textPrimary, weight: FontWeight.w600),
        titleLarge:     t(textPrimary, weight: FontWeight.w600),
        titleMedium:    t(textPrimary, weight: FontWeight.w600),
        titleSmall:     t(textPrimary, weight: FontWeight.w500),
        bodyLarge:      t(textPrimary),
        bodyMedium:     t(textPrimary),
        bodySmall:      t(textSecondary),
        labelLarge:     t(textSecondary),
        labelMedium:    t(textSecondary),
        labelSmall:     t(textMuted),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: bgBase,
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        iconTheme: const IconThemeData(color: textPrimary),
        titleTextStyle: t(textPrimary,
          weight: FontWeight.w600,
          size: 18,
          spacing: -0.3,
        ),
      ),
      colorScheme: const ColorScheme.dark(
        primary: pink400,
        secondary: pink300,
        surface: bgSurface,
        error: error,
        onPrimary: textPrimary,
        onSecondary: textPrimary,
        onSurface: textPrimary,
        onError: textPrimary,
      ).copyWith(surface: bgBase),
      splashFactory: InkRipple.splashFactory,
      highlightColor: Colors.transparent,
    );
  }
}
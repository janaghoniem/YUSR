import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class AuraTheme {
  static const Color pink50 = Color(0xFFFFF0F8);
  static const Color pink100 = Color(0xFFFFD6EF);
  static const Color pink200 = Color(0xFFFFAEDD);
  static const Color pink300 = Color(0xFFFF80C0);
  static const Color pink400 = Color(0xFFFF3D9A);
  static const Color pink500 = Color(0xFFE8007F);
  static const Color pink600 = Color(0xFFC4006B);
  static const Color pink700 = Color(0xFF9A0054);
  static const Color pink800 = Color(0xFF700040);
  static const Color pink900 = Color(0xFF47002A);

  static const Color bgBase = Color(0xFF0F0C0A);
  static const Color bgSurface = Color(0xFF19140F);
  static const Color bgElevated = Color(0xFF231B15);
  static const Color bgOverlay = Color(0xFF2D2319);
  static const Color bgMuted = Color(0xFF3D3028);

  static const Color textPrimary = Color(0xFFF5F0EB);
  static const Color textSecondary = Color(0xFFBFB3A8);
  static const Color textMuted = Color(0xFF8A7D72);
  static const Color textDisabled = Color(0xFF5A5048);

  static const Color error = Color(0xFFFF6B6B);
  static const Color success = Color(0xFF4DD68C);
  static const Color warning = Color(0xFFFFBA44);

  static ThemeData get darkTheme {
    return ThemeData(
      brightness: Brightness.dark,
      primaryColor: pink400,
      scaffoldBackgroundColor: bgBase,
      canvasColor: bgSurface,
      cardColor: bgElevated,
      textTheme: GoogleFonts.dmSansTextTheme(
        ThemeData.dark().textTheme,
      ).copyWith(
        displayLarge: GoogleFonts.dmSans(
          color: textPrimary,
          fontWeight: FontWeight.w600,
        ),
        displayMedium: GoogleFonts.dmSans(
          color: textPrimary,
          fontWeight: FontWeight.w600,
        ),
        displaySmall: GoogleFonts.dmSans(
          color: textPrimary,
          fontWeight: FontWeight.w600,
        ),
        headlineLarge: GoogleFonts.dmSans(
          color: textPrimary,
          fontWeight: FontWeight.w600,
        ),
        headlineMedium: GoogleFonts.dmSans(
          color: textPrimary,
          fontWeight: FontWeight.w600,
        ),
        headlineSmall: GoogleFonts.dmSans(
          color: textPrimary,
          fontWeight: FontWeight.w600,
        ),
        titleLarge: GoogleFonts.dmSans(
          color: textPrimary,
          fontWeight: FontWeight.w600,
        ),
        titleMedium: GoogleFonts.dmSans(
          color: textPrimary,
          fontWeight: FontWeight.w600,
        ),
        titleSmall: GoogleFonts.dmSans(
          color: textPrimary,
          fontWeight: FontWeight.w600,
        ),
        bodyLarge: GoogleFonts.dmSans(color: textPrimary),
        bodyMedium: GoogleFonts.dmSans(color: textPrimary),
        bodySmall: GoogleFonts.dmSans(color: textSecondary),
        labelLarge: GoogleFonts.dmSans(color: textSecondary),
        labelMedium: GoogleFonts.dmSans(color: textSecondary),
        labelSmall: GoogleFonts.dmSans(color: textMuted),
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: bgBase,
        elevation: 0,
        iconTheme: IconThemeData(color: textPrimary),
        titleTextStyle: TextStyle(
          color: textPrimary,
          fontSize: 20,
          fontWeight: FontWeight.w600,
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
    );
  }

  static const Color pink400Dull = Color(0x7FFF3D9A);
}

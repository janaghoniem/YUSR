// lib/screens/android_login_screen.dart
import 'package:flutter/material.dart';
import 'dart:ui' as ui;
import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'dart:convert';
import 'package:video_player/video_player.dart';
import '../theme.dart';
import '../services/auth_service.dart';
import '../services/session_store.dart';
import 'face_scan_screen.dart';
import 'android_onboarding_flow.dart';
import 'startup_screen.dart';

TextStyle _f(
  Color color, {
  FontWeight weight = FontWeight.w400,
  double size = 14,
  double spacing = 0,
  double height = 1.45,
}) => TextStyle(
  fontFamily: 'PlusJakartaSans',
  color: color,
  fontWeight: weight,
  fontSize: size,
  letterSpacing: spacing,
  height: height,
);

class AndroidLoginScreen extends StatefulWidget {
  final void Function(
    String userId,
    String username,
    String sessionId,
    String language,
  )
  onLoginSuccess;

  const AndroidLoginScreen({super.key, required this.onLoginSuccess});

  @override
  State<AndroidLoginScreen> createState() => _AndroidLoginScreenState();
}

class _AndroidLoginScreenState extends State<AndroidLoginScreen>
    with SingleTickerProviderStateMixin {
  late VideoPlayerController _videoCtrl;
  late AnimationController _pulseCtrl;
  bool _cardVisible = false;
  bool _loading = false;
  String? _error;

  // Password fallback
  bool _showPasswordForm = false;
  final TextEditingController _usernameCtrl = TextEditingController();
  final TextEditingController _passwordCtrl = TextEditingController();
  bool _obscurePassword = true;

  // REQ 8: TTS platform channel
  static const _tts = MethodChannel('com.example.automation/tts');
  final FlutterTts _fallbackTts = FlutterTts();

  @override
  void initState() {
    super.initState();
    _fallbackTts.setLanguage('en-US').catchError((_) {});
    _fallbackTts.setSpeechRate(0.46).catchError((_) {});
    _fallbackTts.setPitch(1.0).catchError((_) {});
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 2000),
    )..repeat(reverse: true);

    // REQ 3: aura.mp4 for login screen
    _videoCtrl =
        VideoPlayerController.asset('assets/aura.mp4')
          ..setLooping(true)
          ..initialize().then((_) {
            if (mounted) {
              setState(() {});
              _videoCtrl.play();
            }
          });

    // REQ 8: read screen aloud on appearance
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        setState(() => _cardVisible = true);
      }
      _speakTTS(
        'Welcome back to AURA. Tap the button to sign in with your face.',
      );
    });
  }

  Future<void> _speakTTS(String text) async {
    try {
      await _tts.invokeMethod('speak', {'text': text});
    } catch (_) {
      try {
        await _fallbackTts.stop();
        await _fallbackTts.setSpeechRate(0.46);
        await _fallbackTts.setPitch(1.0);
        await _fallbackTts.speak(text);
      } catch (_) {}
    }
  }

  Future<void> _goBack() async {
    if (Navigator.of(context).canPop()) {
      Navigator.of(context).pop();
      return;
    }
    Navigator.of(context).pushReplacement(
      PageRouteBuilder(
        pageBuilder: (_, __, ___) => const StartupScreen(),
        transitionsBuilder:
            (_, a, __, child) => FadeTransition(opacity: a, child: child),
        transitionDuration: const Duration(milliseconds: 320),
      ),
    );
  }

  @override
  void dispose() {
    _videoCtrl.dispose();
    _pulseCtrl.dispose();
    _fallbackTts.stop();
    _usernameCtrl.dispose();
    _passwordCtrl.dispose();
    super.dispose();
  }

  Future<String> loadAssetAsBase64(String assetPath) async {
    final ByteData data = await rootBundle.load(assetPath);
    final Uint8List bytes = data.buffer.asUint8List();
    final String base64String = base64Encode(bytes);
    return 'data:image/jpeg;base64,$base64String';
  }

  Future<void> _loginWithPassword() async {
    final username = _usernameCtrl.text.trim();
    final password = _passwordCtrl.text.trim();
    if (username.isEmpty || password.isEmpty) {
      setState(() => _error = 'Please enter your username and password.');
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await AuthService.loginWithPassword(
        username: username,
        password: password,
      );
      final userId = result['user_id'] as String;
      final uname = result['username'] as String;
      final prefs = result['preferences'] as Map<String, dynamic>? ?? {};
      final language =
          (prefs['language'] == 'العربية' || prefs['language'] == 'ar')
              ? 'ar'
              : 'en';
      final sessionId = await AuthService.createSession(userId);
      await SessionStore.save(
        userId: userId,
        username: uname,
        sessionId: sessionId,
        language: language,
      );
      if (mounted) {
        await _speakTTS('Welcome back, $uname!');
        widget.onLoginSuccess(userId, uname, sessionId, language);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = e.toString().replaceFirst('Exception: ', '');
        });
        await _speakTTS('Sign in failed. ${_error ?? 'Please try again.'}');
      }
    }
  }

  Future<void> _startFaceLogin() async {
    setState(() {
      _loading = false;
      _error = null;
    });

    final String? base64Image = await Navigator.of(context).push<String?>(
      MaterialPageRoute(
        builder:
            (_) => const FaceScanScreen(
              title: 'Welcome Back',
              subtitle: 'Look at the camera to sign in',
            ),
      ),
    );

    if (base64Image == null || !mounted) return;

    setState(() => _loading = true);

    try {
      final result = await AuthService.loginFaceOnly(base64Image);

      final userId = result['user_id'] as String;
      final username = result['username'] as String;
      final prefs = result['preferences'] as Map<String, dynamic>? ?? {};
      final language =
          (prefs['language'] == 'العربية' || prefs['language'] == 'ar')
              ? 'ar'
              : 'en';

      final sessionId = await AuthService.createSession(userId);

      await SessionStore.save(
        userId: userId,
        username: username,
        sessionId: sessionId,
        language: language,
      );

      if (mounted) {
        await _speakTTS('Welcome back, $username!');
        widget.onLoginSuccess(userId, username, sessionId, language);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = e.toString().replaceFirst('Exception: ', '');
        });
        await _speakTTS('Sign in failed. ${_error ?? 'Please try again.'}');
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AuraTheme.bgBase,
      body: Stack(
        fit: StackFit.expand,
        children: [
          // REQ 3: aura.mp4 video background
          if (_videoCtrl.value.isInitialized)
            FittedBox(
              fit: BoxFit.cover,
              child: SizedBox(
                width: _videoCtrl.value.size.width,
                height: _videoCtrl.value.size.height,
                child: VideoPlayer(_videoCtrl),
              ),
            ),
          Container(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Colors.black.withOpacity(0.2),
                  Colors.black.withOpacity(0.5),
                ],
              ),
            ),
          ),

          SafeArea(
            child: Align(
              alignment: Alignment.bottomCenter,
              child: TweenAnimationBuilder<double>(
                tween: Tween(begin: 0.12, end: _cardVisible ? 0 : 0.12),
                duration: const Duration(milliseconds: 560),
                curve: Curves.easeOutCubic,
                builder: (context, offsetY, child) {
                  return Transform.translate(
                    offset: Offset(0, offsetY * 220),
                    child: child,
                  );
                },
                child: ClipRRect(
                  borderRadius: const BorderRadius.vertical(
                    top: Radius.circular(34),
                  ),
                  child: BackdropFilter(
                    filter: ui.ImageFilter.blur(sigmaX: 16, sigmaY: 16),
                    child: Container(
                      width: double.infinity,
                      constraints: BoxConstraints(
                        minHeight: MediaQuery.of(context).size.height * 0.9,
                        maxHeight: MediaQuery.of(context).size.height * 0.9,
                      ),
                      decoration: BoxDecoration(
                        color: Colors.black.withOpacity(0.14),
                        border: Border(
                          top: BorderSide(
                            color: Colors.white.withOpacity(0.15),
                            width: 1,
                          ),
                        ),
                      ),
                      child: SingleChildScrollView(
                        padding: const EdgeInsets.fromLTRB(24, 26, 24, 24),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.center,
                          children: [
                            Row(
                              children: [
                                GestureDetector(
                                  onTap: _goBack,
                                  child: Container(
                                    padding: const EdgeInsets.all(9),
                                    decoration: BoxDecoration(
                                      color: Colors.white.withOpacity(0.08),
                                      borderRadius: BorderRadius.circular(12),
                                      border: Border.all(
                                        color: Colors.white.withOpacity(0.14),
                                      ),
                                    ),
                                    child: const Icon(
                                      Icons.arrow_back_rounded,
                                      color: Colors.white,
                                      size: 18,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 10),
                            Container(
                              width: 44,
                              height: 4,
                              decoration: BoxDecoration(
                                color: Colors.white.withOpacity(0.28),
                                borderRadius: BorderRadius.circular(999),
                              ),
                            ),
                            const SizedBox(height: 25),
                            Text(
                              'Face ID sign in',
                              style: _f(
                                AuraTheme.textPrimary,
                                size: 33,
                                weight: FontWeight.w500,
                                spacing: 0.4,
                              ),
                            ),
                            const SizedBox(height: 8),
                            Text(
                              'Tap below and look at the camera to sign in securely with your face.',
                              style: _f(AuraTheme.textSecondary, size: 13),
                              textAlign: TextAlign.center,
                            ),
                            const SizedBox(height: 45),

                            if (_error != null)
                              Padding(
                                padding: const EdgeInsets.only(bottom: 16),
                                child: Container(
                                  width: double.infinity,
                                  padding: const EdgeInsets.all(12),
                                  decoration: BoxDecoration(
                                    color: AuraTheme.error.withOpacity(0.12),
                                    borderRadius: BorderRadius.circular(14),
                                    border: Border.all(
                                      color: AuraTheme.error.withOpacity(0.35),
                                    ),
                                  ),
                                  child: Text(
                                    _error!,
                                    style: _f(AuraTheme.error, size: 12),
                                    textAlign: TextAlign.center,
                                  ),
                                ),
                              ),

                            GestureDetector(
                              onTap: _loading ? null : _startFaceLogin,
                              child: AnimatedBuilder(
                                animation: _pulseCtrl,
                                builder: (_, __) {
                                  final glow = 0.08 + (_pulseCtrl.value * 0.07);
                                  return Container(
                                    width: double.infinity,
                                    padding: const EdgeInsets.fromLTRB(
                                      20,
                                      18,
                                      20,
                                      14,
                                    ),
                                    decoration: BoxDecoration(
                                      color: Colors.white.withOpacity(0.04),
                                      borderRadius: BorderRadius.circular(24),
                                      border: Border.all(
                                        color: Colors.white.withOpacity(0.14),
                                      ),
                                      boxShadow: [
                                        BoxShadow(
                                          color: Colors.white.withOpacity(glow),
                                          blurRadius: 18,
                                          offset: const Offset(0, 5),
                                        ),
                                      ],
                                    ),
                                    child: Column(
                                      children: [
                                        _loading
                                            ? const SizedBox(
                                              width: 44,
                                              height: 44,
                                              child: CircularProgressIndicator(
                                                strokeWidth: 2,
                                                color: Colors.white,
                                              ),
                                            )
                                            : Image.asset(
                                              'assets/face_id_icon_transparent.png',
                                              width: 170,
                                              height: 170,
                                              fit: BoxFit.contain,
                                            ),
                                        const SizedBox(height: 25),
                                        Text(
                                          _loading
                                              ? 'Verifying...'
                                              : 'Tap to sign in with Face ID',
                                          style: _f(
                                            AuraTheme.pink400,
                                            size: 13,
                                          ),
                                        ),
                                        const SizedBox(height: 20),
                                        Text(
                                          'Your face data is encrypted and secure',
                                          style: _f(
                                            AuraTheme.textSecondary,
                                            size: 11,
                                            weight: FontWeight.w500,
                                          ),
                                          textAlign: TextAlign.center,
                                        ),
                                      ],
                                    ),
                                  );
                                },
                              ),
                            ),

                            const SizedBox(height: 70),
                            _AuraButton(
                              label: 'Sign In with Face',
                              icon: Icons.login_rounded,
                              onTap: _loading ? null : _startFaceLogin,
                              loading: _loading,
                              isPrimary: false,
                            ),
                            const SizedBox(height: 20),

// ── Password fallback ──────────────────────────
                            AnimatedSize(
                              duration: const Duration(milliseconds: 320),
                              curve: Curves.easeOut,
                              child: _showPasswordForm
                                  ? Column(
                                      children: [
                                        const SizedBox(height: 20),
                                        // username field
                                        ClipRRect(
                                          borderRadius: BorderRadius.circular(16),
                                          child: BackdropFilter(
                                            filter: ui.ImageFilter.blur(sigmaX: 12, sigmaY: 12),
                                            child: Container(
                                              decoration: BoxDecoration(
                                                color: Colors.white.withOpacity(0.05),
                                                borderRadius: BorderRadius.circular(16),
                                                border: Border.all(color: Colors.white.withOpacity(0.12)),
                                              ),
                                              child: TextField(
                                                controller: _usernameCtrl,
                                                style: _f(AuraTheme.textPrimary, size: 15),
                                                decoration: InputDecoration(
                                                  hintText: 'Username',
                                                  hintStyle: _f(AuraTheme.textMuted, size: 14),
                                                  contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
                                                  border: InputBorder.none,
                                                ),
                                              ),
                                            ),
                                          ),
                                        ),
                                        const SizedBox(height: 12),
                                        // password field
                                        ClipRRect(
                                          borderRadius: BorderRadius.circular(16),
                                          child: BackdropFilter(
                                            filter: ui.ImageFilter.blur(sigmaX: 12, sigmaY: 12),
                                            child: Container(
                                              decoration: BoxDecoration(
                                                color: Colors.white.withOpacity(0.05),
                                                borderRadius: BorderRadius.circular(16),
                                                border: Border.all(color: Colors.white.withOpacity(0.12)),
                                              ),
                                              child: TextField(
                                                controller: _passwordCtrl,
                                                obscureText: _obscurePassword,
                                                style: _f(AuraTheme.textPrimary, size: 15),
                                                onSubmitted: (_) => _loginWithPassword(),
                                                decoration: InputDecoration(
                                                  hintText: 'Password',
                                                  hintStyle: _f(AuraTheme.textMuted, size: 14),
                                                  contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
                                                  border: InputBorder.none,
                                                  suffixIcon: GestureDetector(
                                                    onTap: () => setState(() => _obscurePassword = !_obscurePassword),
                                                    child: Icon(
                                                      _obscurePassword ? Icons.visibility_off_rounded : Icons.visibility_rounded,
                                                      color: Colors.white.withOpacity(0.45),
                                                      size: 20,
                                                    ),
                                                  ),
                                                ),
                                              ),
                                            ),
                                          ),
                                        ),
                                        const SizedBox(height: 16),
                                        _AuraButton(
                                          label: 'Sign In →',
                                          icon: Icons.login_rounded,
                                          onTap: _loading ? null : _loginWithPassword,
                                          loading: _loading,
                                          isPrimary: true,
                                        ),
                                        const SizedBox(height: 12),
                                        GestureDetector(
                                          onTap: () => setState(() {
                                            _showPasswordForm = false;
                                            _error = null;
                                          }),
                                          child: Text(
                                            '← Back to Face ID',
                                            style: _f(AuraTheme.textSecondary, size: 13),
                                          ),
                                        ),
                                      ],
                                    )
                                  : GestureDetector(
                                      onTap: () => setState(() {
                                        _showPasswordForm = true;
                                        _error = null;
                                      }),
                                      child: Padding(
                                        padding: const EdgeInsets.symmetric(vertical: 6),
                                        child: Text(
                                          'Use password instead',
                                          style: _f(
                                            AuraTheme.textSecondary,
                                            size: 13,
                                            weight: FontWeight.w500,
                                          ),
                                        ),
                                      ),
                                    ),
                            ),
                            const SizedBox(height: 12),
                            GestureDetector(
                              onTap: () {
                                Navigator.of(context).push(
                                  MaterialPageRoute(
                                    builder:
                                        (_) => AndroidOnboardingFlow(
                                          onComplete: (
                                            userId,
                                            username,
                                            sessionId,
                                            language,
                                          ) {
                                            widget.onLoginSuccess(
                                              userId,
                                              username,
                                              sessionId,
                                              language,
                                            );
                                          },
                                        ),
                                  ),
                                );
                              },
                              child: Padding(
                                padding: const EdgeInsets.symmetric(
                                  vertical: 6,
                                ),
                                child: Text(
                                  'Create account',
                                  style: _f(
                                    AuraTheme.textSecondary,
                                    size: 14,
                                    weight: FontWeight.w500,
                                    spacing: 0.1,
                                  ),
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// REQ 11: unified button widget used across the app
class _AuraButton extends StatelessWidget {
  final String label;
  final IconData? icon;
  final VoidCallback? onTap;
  final bool loading;
  final bool isPrimary;

  const _AuraButton({
    required this.label,
    this.icon,
    this.onTap,
    this.loading = false,
    required this.isPrimary,
  });

  @override
  Widget build(BuildContext context) {
    final enabled = onTap != null;
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        width: double.infinity,
        height: 54,
        decoration: BoxDecoration(
          color:
              enabled
                  ? Colors.white.withOpacity(0.06)
                  : Colors.white.withOpacity(0.03),
          borderRadius: BorderRadius.circular(28),
          border: Border.all(
            color:
                enabled
                    ? AuraTheme.pink400.withOpacity(0.55)
                    : AuraTheme.pink400.withOpacity(0.2),
            width: 1.2,
          ),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(0.2),
              blurRadius: 10,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child:
            loading
                ? const Center(
                  child: SizedBox(
                    width: 22,
                    height: 22,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  ),
                )
                : Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    if (icon != null) ...[
                      Icon(
                        icon,
                        color: AuraTheme.pink300.withOpacity(
                          enabled ? 0.95 : 0.45,
                        ),
                        size: 19,
                      ),
                      const SizedBox(width: 10),
                    ],
                    Text(
                      label,
                      style: TextStyle(
                        fontFamily: 'PlusJakartaSans',
                        color: AuraTheme.pink300.withOpacity(
                          enabled ? 0.95 : 0.45,
                        ),
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                        letterSpacing: 0.3,
                      ),
                    ),
                  ],
                ),
      ),
    );
  }
}

import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';
import '../theme.dart';
import '../services/auth_service.dart';
import '../services/session_store.dart';
import 'face_scan_screen.dart';
import 'android_onboarding_flow.dart';

TextStyle _f(Color color,
    {FontWeight weight = FontWeight.w400,
    double size = 14,
    double spacing = 0,
    double height = 1.45}) =>
    TextStyle(
      fontFamily: 'PlusJakartaSans',
      color: color,
      fontWeight: weight,
      fontSize: size,
      letterSpacing: spacing,
      height: height,
    );

class AndroidLoginScreen extends StatefulWidget {
  final void Function(
      String userId, String username, String sessionId, String language)
      onLoginSuccess;

  const AndroidLoginScreen({
    super.key,
    required this.onLoginSuccess,
  });

  @override
  State<AndroidLoginScreen> createState() => _AndroidLoginScreenState();
}

class _AndroidLoginScreenState extends State<AndroidLoginScreen> {
  late VideoPlayerController _videoCtrl;
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _videoCtrl = VideoPlayerController.asset('assets/aura1.webm')
      ..setLooping(true)
      ..initialize().then((_) {
        if (mounted) {
          setState(() {});
          _videoCtrl.play();
        }
      });
  }

  @override
  void dispose() {
    _videoCtrl.dispose();
    super.dispose();
  }

  Future<void> _startFaceLogin() async {
    setState(() {
      _loading = false;
      _error = null;
    });

    final String? base64Image = await Navigator.of(context).push<String?>(
      MaterialPageRoute(
        builder: (_) => const FaceScanScreen(
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

      if (mounted) widget.onLoginSuccess(userId, username, sessionId, language);
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = e.toString().replaceFirst('Exception: ', '');
        });
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
          if (_videoCtrl.value.isInitialized)
            FittedBox(
              fit: BoxFit.cover,
              child: SizedBox(
                width: _videoCtrl.value.size.width,
                height: _videoCtrl.value.size.height,
                child: VideoPlayer(_videoCtrl),
              ),
            ),
          SafeArea(
            child: Column(
              children: [
                const Spacer(),
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text('AURA',
                      style: _f(AuraTheme.textPrimary,
                          size: 34,
                          weight: FontWeight.w500,
                          spacing: 1.5)),
                ),
                Text('Your intelligent assistant',
                    style: _f(AuraTheme.textSecondary, size: 14)),
                const Spacer(),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 32, vertical: 8),
                    child: Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: AuraTheme.error.withOpacity(0.1),
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(
                            color: AuraTheme.error.withOpacity(0.4)),
                      ),
                      child: Text(_error!,
                          style: _f(AuraTheme.error, size: 13),
                          textAlign: TextAlign.center),
                    ),
                  ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 32),
                  child: GestureDetector(
                    onTap: _loading ? null : _startFaceLogin,
                    child: Container(
                      width: double.infinity,
                      padding: const EdgeInsets.symmetric(vertical: 18),
                      decoration: BoxDecoration(
                        gradient: _loading
                            ? null
                            : const LinearGradient(
                                colors: [
                                  AuraTheme.pink500,
                                  AuraTheme.pink700
                                ],
                              ),
                        color: _loading ? AuraTheme.bgElevated : null,
                        borderRadius: BorderRadius.circular(30),
                        boxShadow: _loading
                            ? null
                            : [
                                BoxShadow(
                                  color: AuraTheme.pink500.withOpacity(0.4),
                                  blurRadius: 20,
                                  offset: const Offset(0, 6),
                                )
                              ],
                      ),
                      alignment: Alignment.center,
                      child: _loading
                          ? const SizedBox(
                              height: 22,
                              width: 22,
                              child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: AuraTheme.pink400))
                          : Row(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                const Icon(Icons.face_retouching_natural,
                                    color: Colors.white, size: 20),
                                const SizedBox(width: 10),
                                Text('Sign in with Face',
                                    style: _f(Colors.white,
                                        size: 15,
                                        weight: FontWeight.w700)),
                              ],
                            ),
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                Padding(
                  padding: const EdgeInsets.only(bottom: 48),
                  child: TextButton(
                    onPressed: () {
                      Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (_) => AndroidOnboardingFlow(
                            onComplete: (userId, username, sessionId, language) {
                              widget.onLoginSuccess(
                                  userId, username, sessionId, language);
                            },
                          ),
                        ),
                      );
                    },
                    style: TextButton.styleFrom(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 24, vertical: 12),
                      tapTargetSize: MaterialTapTargetSize.padded,
                    ),
                    child: Text(
                      'New here? Create an account',
                      style: _f(AuraTheme.pink300,
                          size: 13, weight: FontWeight.w500),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
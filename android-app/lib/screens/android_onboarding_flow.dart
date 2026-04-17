// lib/screens/android_onboarding_flow.dart
import 'dart:ui';
import 'dart:math';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;
import 'package:video_player/video_player.dart';
import '../theme.dart';
import '../services/auth_service.dart';
import '../services/session_store.dart';
import 'face_scan_screen.dart';

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

// REQ 11: Shared AURA button widget
class AuraButton extends StatelessWidget {
  final String label;
  final IconData? icon;
  final VoidCallback? onTap;
  final bool loading;
  final bool isPrimary;
  final bool isDestructive;

  const AuraButton({
    super.key,
    required this.label,
    this.icon,
    this.onTap,
    this.loading = false,
    this.isPrimary = true,
    this.isDestructive = false,
  });

  @override
  Widget build(BuildContext context) {
    final Color accentColor =
        isDestructive ? Colors.redAccent : AuraTheme.pink500;

    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        width: double.infinity,
        height: 54,
        decoration: BoxDecoration(
          gradient:
              isPrimary && !isDestructive && onTap != null
                  ? LinearGradient(
                    colors: [accentColor, AuraTheme.pink700],
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                  )
                  : null,
          color:
              isDestructive
                  ? Colors.redAccent.withOpacity(0.12)
                  : (!isPrimary ? Colors.white.withOpacity(0.06) : null),
          borderRadius: BorderRadius.circular(28),
          border: Border.all(
            color:
                isDestructive
                    ? Colors.redAccent.withOpacity(0.4)
                    : isPrimary
                    ? Colors.white.withOpacity(0.15)
                    : AuraTheme.pink400.withOpacity(0.45),
            width: 1.2,
          ),
          boxShadow:
              isPrimary && !isDestructive && onTap != null
                  ? [
                    BoxShadow(
                      color: accentColor.withOpacity(0.3),
                      blurRadius: 16,
                      offset: const Offset(0, 4),
                    ),
                  ]
                  : null,
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
                        color: isDestructive ? Colors.redAccent : Colors.white,
                        size: 18,
                      ),
                      const SizedBox(width: 10),
                    ],
                    Text(
                      label,
                      style: TextStyle(
                        fontFamily: 'PlusJakartaSans',
                        color:
                            isDestructive
                                ? Colors.redAccent
                                : isPrimary
                                ? Colors.white
                                : AuraTheme.pink300,
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
      ),
    );
  }
}

class AndroidOnboardingFlow extends StatefulWidget {
  final void Function(
    String userId,
    String username,
    String sessionId,
    String language,
  )
  onComplete;

  const AndroidOnboardingFlow({super.key, required this.onComplete});

  @override
  State<AndroidOnboardingFlow> createState() => _AndroidOnboardingFlowState();
}

class _AndroidOnboardingFlowState extends State<AndroidOnboardingFlow>
    with TickerProviderStateMixin {
  String _language = 'en';
  final Map<String, String> _answers = {
    'name': '',
    'job': '',
    'accessibility': '',
    'tasks': '',
  };

  // Flow: -1=intro, 0=questions, 1=username, 2=accessibility, 3=face, 4=creating
  int _flowStep = -1;

  final TextEditingController _usernameCtrl = TextEditingController();
  bool _usernameChecking = false;
  bool? _usernameAvailable;
  String? _usernameError;

  String? _faceBase64;
  bool _creatingAccount = false;
  String? _creationError;

  String _userId = '';

  // REQ 4: accessibility
  bool _accessibilityGranted = false;
  static const _platform = MethodChannel('com.example.automation/service');
  static const _tts = MethodChannel('com.example.automation/tts');
  final FlutterTts _fallbackTts = FlutterTts();

  // REQ 3: aura.mp4 for login/onboarding, aura_calm.webm for question steps
  VideoPlayerController? _videoCtrl;

  late AnimationController _waveCtrl;

  @override
  void initState() {
    super.initState();
    _fallbackTts.setLanguage('en-US').catchError((_) {});
    _fallbackTts.setSpeechRate(0.46).catchError((_) {});
    _fallbackTts.setPitch(1.0).catchError((_) {});
    _userId = AuthService.generateUserId();
    _waveCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);
    _initVideo(isIntro: true);
  }

  Future<void> _initVideo({bool isIntro = false}) async {
    _videoCtrl?.dispose();
    final asset = isIntro ? 'assets/aura.mp4' : 'assets/aura_main.mp4';
    _videoCtrl =
        VideoPlayerController.asset(asset)
          ..setLooping(true)
          ..initialize().then((_) {
            if (mounted) {
              setState(() {});
              _videoCtrl?.play();
            }
          });
  }

  // REQ 8: TTS speak
  Future<void> _speak(String text) async {
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

  // REQ 4: request accessibility once
  Future<void> _requestAccessibility() async {
    try {
      await _platform.invokeMethod('openAccessibilitySettings');
      // Wait briefly then check
      await Future.delayed(const Duration(seconds: 2));
      final bool enabled = await _platform.invokeMethod('isServiceEnabled');
      setState(() => _accessibilityGranted = enabled);
    } catch (_) {}
  }

  Future<void> _checkAccessibility() async {
    try {
      final bool enabled = await _platform.invokeMethod('isServiceEnabled');
      setState(() => _accessibilityGranted = enabled);
    } catch (_) {}
  }

  void _checkUsername(String val) {
    setState(() {
      _usernameAvailable = val.trim().length >= 2;
      _usernameError = null;
    });
  }

  void _handleBackFromFlow() {
    if (_flowStep > -1) {
      setState(() => _flowStep = _flowStep - 1);
      return;
    }
    if (Navigator.of(context).canPop()) {
      Navigator.of(context).pop();
    }
  }

  @override
  void dispose() {
    _usernameCtrl.dispose();
    _videoCtrl?.dispose();
    _waveCtrl.dispose();
    _fallbackTts.stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 450),
      switchInCurve: Curves.easeOut,
      switchOutCurve: Curves.easeIn,
      transitionBuilder:
          (child, animation) =>
              FadeTransition(opacity: animation, child: child),
      child: switch (_flowStep) {
        -1 => KeyedSubtree(
          key: const ValueKey('intro'),
          child: _buildIntroStep(),
        ),
        0 => KeyedSubtree(
          key: const ValueKey('questions'),
          child: _buildQuestionsStep(),
        ),
        1 => KeyedSubtree(
          key: const ValueKey('username'),
          child: _buildUsernameStep(),
        ),
        2 => KeyedSubtree(
          key: const ValueKey('accessibility'),
          child: _buildAccessibilityStep(),
        ),
        _ => KeyedSubtree(key: const ValueKey('face'), child: _buildFaceStep()),
      },
    );
  }

  // ── Step -1: Cinematic Intro ──────────────────────────────────────────────

  Widget _buildIntroStep() {
    return _CinematicIntroScreen(
      videoCtrl: _videoCtrl,
      onBack: _handleBackFromFlow,
      onComplete: () {
        _initVideo(isIntro: false); // switch to calm bg for questions
        setState(() => _flowStep = 0);
      },
      onSpeak: _speak,
    );
  }

  // ── Step 0: Questions ─────────────────────────────────────────────────────

  Widget _buildQuestionsStep() {
    return _VoiceQuestionFlow(
      videoCtrl: _videoCtrl,
      waveCtrl: _waveCtrl,
      onBack: _handleBackFromFlow,
      onComplete: (String language, Map<String, String> answers) {
        setState(() {
          _language = language;
          _answers.addAll(answers);
          final nameFromAnswers = answers['name'] ?? '';
          if (nameFromAnswers.isNotEmpty && _usernameCtrl.text.isEmpty) {
            _usernameCtrl.text = nameFromAnswers;
            _checkUsername(_usernameCtrl.text);
          }
          _flowStep = 1;
        });
      },
      onSpeak: _speak,
    );
  }

  // ── Step 1: Username ──────────────────────────────────────────────────────

  Widget _buildUsernameStep() {
    // REQ 8: read on appear
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _speak(
        _answers['name']?.isNotEmpty == true
            ? 'Almost there, ${_answers['name']}! What should we call you?'
            : 'What should we call you?',
      );
    });

    return _buildVideoScaffold(
      onBack: _handleBackFromFlow,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SizedBox(height: 20),
            BlurRevealText(
              text:
                  _answers['name']?.isNotEmpty == true
                      ? 'Almost there, ${_answers['name']}!'
                      : 'What should we call you?',
              style: _f(
                AuraTheme.textPrimary,
                size: 28,
                weight: FontWeight.w600,
              ),
              revealProgress: 1.0,
              duration: const Duration(milliseconds: 900),
            ),
            const SizedBox(height: 8),
            Text(
              'You can edit this any time.',
              style: _f(AuraTheme.textSecondary, size: 13),
            ),
            const SizedBox(height: 32),

            // REQ 15: glassmorphic input
            _GlassTextField(
              controller: _usernameCtrl,
              hint: 'e.g. sara',
              isValid: _usernameAvailable == true,
              hasError: _usernameError != null,
              onChanged: _checkUsername,
              suffix:
                  _usernameChecking
                      ? const Padding(
                        padding: EdgeInsets.all(14),
                        child: SizedBox(
                          width: 14,
                          height: 14,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: AuraTheme.pink400,
                          ),
                        ),
                      )
                      : _usernameAvailable == true
                      ? const Icon(
                        Icons.check_circle_rounded,
                        color: AuraTheme.success,
                        size: 20,
                      )
                      : null,
            ),
            if (_usernameError != null)
              Padding(
                padding: const EdgeInsets.only(top: 8, left: 4),
                child: Text(
                  _usernameError!,
                  style: _f(AuraTheme.error, size: 12),
                ),
              ),
            if (_usernameAvailable == true)
              Padding(
                padding: const EdgeInsets.only(top: 6, left: 4),
                child: Text(
                  '✓ Looks good',
                  style: _f(
                    AuraTheme.success,
                    size: 12,
                    weight: FontWeight.w600,
                  ),
                ),
              ),

            const Spacer(),
            AuraButton(
              label: 'Continue →',
              onTap:
                  (_usernameCtrl.text.trim().length >= 2)
                      ? () => setState(() => _flowStep = 2)
                      : null,
            ),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
  }

  // ── Step 2: Accessibility permission (REQ 4) ──────────────────────────────

  Widget _buildAccessibilityStep() {
    // REQ 8: speak on appear
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _speak(
        'To allow AURA to automate tasks, please enable the Accessibility Service. You can always toggle this in Settings.',
      );
    });

    return _buildVideoScaffold(
      onBack: _handleBackFromFlow,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SizedBox(height: 20),
            BlurRevealText(
              text: 'One quick permission',
              style: _f(
                AuraTheme.textPrimary,
                size: 28,
                weight: FontWeight.w600,
              ),
              revealProgress: 1.0,
              duration: const Duration(milliseconds: 900),
            ),
            const SizedBox(height: 8),
            Text(
              'AURA needs Accessibility access to interact with apps on your behalf.',
              style: _f(AuraTheme.textSecondary, size: 14, height: 1.5),
            ),
            const SizedBox(height: 28),

            // REQ 15: glassmorphic info card
            ClipRRect(
              borderRadius: BorderRadius.circular(20),
              child: BackdropFilter(
                filter: ImageFilter.blur(sigmaX: 16, sigmaY: 16),
                child: Container(
                  padding: const EdgeInsets.all(20),
                  decoration: BoxDecoration(
                    color: Colors.white.withOpacity(0.05),
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: Colors.white.withOpacity(0.1)),
                  ),
                  child: Column(
                    children: [
                      _permRow(
                        Icons.phone_android_rounded,
                        'Interact with other apps on screen',
                      ),
                      const SizedBox(height: 14),
                      _permRow(
                        Icons.lock_outline_rounded,
                        'Only used when you ask AURA to act',
                      ),
                      const SizedBox(height: 14),
                      _permRow(
                        Icons.settings_outlined,
                        'Toggle anytime in Settings → Privacy',
                      ),
                    ],
                  ),
                ),
              ),
            ),

            const SizedBox(height: 24),

            if (_accessibilityGranted) ...[
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: AuraTheme.success.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(
                    color: AuraTheme.success.withOpacity(0.35),
                  ),
                ),
                child: Row(
                  children: [
                    const Icon(
                      Icons.check_circle_rounded,
                      color: AuraTheme.success,
                      size: 20,
                    ),
                    const SizedBox(width: 10),
                    Text(
                      'Accessibility enabled!',
                      style: _f(
                        AuraTheme.success,
                        size: 13,
                        weight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
            ],

            const Spacer(),

            if (!_accessibilityGranted)
              AuraButton(
                label: 'Enable Accessibility',
                icon: Icons.accessibility_new_rounded,
                onTap: _requestAccessibility,
              ),
            if (!_accessibilityGranted) const SizedBox(height: 12),
            AuraButton(
              label: _accessibilityGranted ? 'Continue →' : 'Skip for now',
              isPrimary: _accessibilityGranted,
              onTap: () {
                if (!_accessibilityGranted) {
                  // Try checking one more time
                  _checkAccessibility();
                }
                setState(() => _flowStep = 3);
              },
            ),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
  }

  Widget _permRow(IconData icon, String text) {
    return Row(
      children: [
        Container(
          padding: const EdgeInsets.all(8),
          decoration: BoxDecoration(
            color: AuraTheme.pink400.withOpacity(0.12),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Icon(icon, color: AuraTheme.pink400, size: 16),
        ),
        const SizedBox(width: 14),
        Expanded(
          child: Text(text, style: _f(AuraTheme.textSecondary, size: 13)),
        ),
      ],
    );
  }

  // ── Step 3: Face Registration ─────────────────────────────────────────────

  Widget _buildFaceStep() {
    // REQ 8: speak on appear
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _speak(
        'Almost done! Register your face to log in securely without a password.',
      );
    });

    return _buildVideoScaffold(
      onBack: _handleBackFromFlow,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SizedBox(height: 20),
            BlurRevealText(
              text: 'Register your face',
              style: _f(
                AuraTheme.textPrimary,
                size: 28,
                weight: FontWeight.w600,
              ),
              revealProgress: 1.0,
              duration: const Duration(milliseconds: 900),
            ),
            const SizedBox(height: 8),
            Text(
              "You'll use your face to log in — no password needed.",
              style: _f(AuraTheme.textSecondary, size: 13),
            ),
            const Spacer(),
            if (_faceBase64 != null)
              Center(
                child: Column(
                  children: [
                    Container(
                      width: 84,
                      height: 84,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: AuraTheme.success.withOpacity(0.12),
                        border: Border.all(color: AuraTheme.success, width: 2),
                      ),
                      child: const Icon(
                        Icons.check_rounded,
                        color: AuraTheme.success,
                        size: 42,
                      ),
                    ),
                    const SizedBox(height: 14),
                    Text(
                      'Face captured!',
                      style: _f(
                        AuraTheme.success,
                        size: 15,
                        weight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 10),
                    GestureDetector(
                      onTap: () async {
                        final img = await Navigator.of(context).push<String?>(
                          MaterialPageRoute(
                            builder:
                                (_) => const FaceScanScreen(
                                  title: 'Re-scan',
                                  subtitle: 'Retake your face scan',
                                ),
                          ),
                        );
                        if (img != null && mounted)
                          setState(() => _faceBase64 = img);
                      },
                      child: Text(
                        'Retake →',
                        style: _f(AuraTheme.textMuted, size: 12),
                      ),
                    ),
                  ],
                ),
              )
            else
              Center(
                child: GestureDetector(
                  onTap: () async {
                    final img = await Navigator.of(context).push<String?>(
                      MaterialPageRoute(
                        builder:
                            (_) => const FaceScanScreen(
                              title: 'Register Face',
                              subtitle: 'Look directly at the camera',
                            ),
                      ),
                    );
                    if (img != null && mounted) {
                      setState(() => _faceBase64 = img);
                      await _speak(
                        'Face captured successfully! Tap Create Account to finish.',
                      );
                    }
                  },
                  child: _PulseFaceButton(),
                ),
              ),
            const Spacer(),
            if (_creationError != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text(
                  _creationError!,
                  style: _f(AuraTheme.error, size: 12),
                  textAlign: TextAlign.center,
                ),
              ),
            AuraButton(
              label: 'Create Account →',
              onTap:
                  (_faceBase64 != null && !_creatingAccount)
                      ? _createAccount
                      : null,
              loading: _creatingAccount,
            ),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
  }

  // ── Account creation ───────────────────────────────────────────────────────

  Future<void> _createAccount() async {
    if (_faceBase64 == null) return;
    setState(() {
      _creatingAccount = true;
      _creationError = null;
    });

    await _speak('Creating your account. Please wait.');

    try {
      final username = _usernameCtrl.text.trim();
      final introduction = _answers.values
          .where((v) => v.isNotEmpty)
          .join('. ');

      await AuthService.registerFace(
        userId: _userId,
        username: username,
        faceImageBase64: _faceBase64!,
      );

      await AuthService.createAccount(
        userId: _userId,
        username: username,
        introduction: introduction,
        preferences: {
          'language': _language == 'ar' ? 'العربية' : 'English',
          'theme': 'dark',
          'voice': 'Gacrux',
        },
      );

      await AuthService.storeIntroduction(
        userId: _userId,
        language: _language,
        answers: _answers,
      );

      final sessionId = await AuthService.createSession(_userId);

      await SessionStore.save(
        userId: _userId,
        username: username,
        sessionId: sessionId,
        language: _language,
      );

      if (mounted) {
        await _speak("Welcome to AURA, $username! Let's get started.");
        widget.onComplete(_userId, username, sessionId, _language);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _creatingAccount = false;
          _creationError = e.toString().replaceFirst('Exception: ', '');
        });
        await _speak('Account creation failed. ${_creationError ?? ''}');
      }
    }
  }

  // Scaffold with video background
  Widget _buildVideoScaffold({
    required Widget child,
    required VoidCallback onBack,
  }) {
    return Scaffold(
      backgroundColor: AuraTheme.bgBase,
      body: Stack(
        fit: StackFit.expand,
        children: [
          if (_videoCtrl?.value.isInitialized == true)
            FittedBox(
              fit: BoxFit.cover,
              child: SizedBox(
                width: _videoCtrl!.value.size.width,
                height: _videoCtrl!.value.size.height,
                child: VideoPlayer(_videoCtrl!),
              ),
            ),
          Container(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Colors.black.withOpacity(0.2),
                  Colors.black.withOpacity(0.44),
                ],
              ),
            ),
          ),
          Positioned(
            top: -56,
            left: 0,
            right: 0,
            child: Center(
              child: Image.asset(
                'assets/aura_icon_white.png',
                width: 210,
                height: 210,
                fit: BoxFit.contain,
              ),
            ),
          ),
          Positioned(
            left: -40,
            right: -40,
            bottom: -86,
            child: Container(
              height: 260,
              decoration: BoxDecoration(
                gradient: RadialGradient(
                  center: const Alignment(0, 1),
                  radius: 1,
                  colors: [
                    AuraTheme.pink500.withOpacity(0.24),
                    AuraTheme.pink400.withOpacity(0.12),
                    Colors.transparent,
                  ],
                ),
              ),
            ),
          ),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 86, 12, 10),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(30),
                child: BackdropFilter(
                  filter: ImageFilter.blur(sigmaX: 24, sigmaY: 24),
                  child: Container(
                    decoration: BoxDecoration(
                      color: Colors.black.withOpacity(0.22),
                      borderRadius: BorderRadius.circular(30),
                      border: Border.all(color: Colors.white.withOpacity(0.2)),
                    ),
                    child: Column(
                      children: [
                        Padding(
                          padding: const EdgeInsets.fromLTRB(14, 12, 14, 0),
                          child: Row(
                            children: [
                              GestureDetector(
                                onTap: onBack,
                                child: Container(
                                  padding: const EdgeInsets.all(8),
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
                              const Spacer(),
                              Container(
                                width: 44,
                                height: 4,
                                decoration: BoxDecoration(
                                  color: Colors.white.withOpacity(0.28),
                                  borderRadius: BorderRadius.circular(999),
                                ),
                              ),
                              const Spacer(),
                              const SizedBox(width: 34),
                            ],
                          ),
                        ),
                        Expanded(child: child),
                      ],
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

// ── Cinematic Intro Screen ─────────────────────────────────────────────────

class _CinematicIntroScreen extends StatefulWidget {
  final VideoPlayerController? videoCtrl;
  final VoidCallback onBack;
  final VoidCallback onComplete;
  final Future<void> Function(String) onSpeak;

  const _CinematicIntroScreen({
    required this.videoCtrl,
    required this.onBack,
    required this.onComplete,
    required this.onSpeak,
  });

  @override
  State<_CinematicIntroScreen> createState() => _CinematicIntroScreenState();
}

class _CinematicIntroScreenState extends State<_CinematicIntroScreen> {
  int _visibleLines = 0;
  bool _done = false;

  final List<String> _lines = [
    "Hi there. I'm AURA.",
    "Your intelligent assistant — built to help you think, create, and act.",
    "I remember your preferences, learn your habits, and get smarter over time.",
    "Let's set things up together. It only takes a minute.",
  ];

  @override
  void initState() {
    super.initState();
    _startSequence();
    // REQ 8: speak intro
    WidgetsBinding.instance.addPostFrameCallback((_) {
      widget.onSpeak(_lines.join(' '));
    });
  }

  Future<void> _startSequence() async {
    for (int i = 0; i < _lines.length; i++) {
      await Future.delayed(const Duration(milliseconds: 2200));
      if (!mounted) return;
      setState(() => _visibleLines = i + 1);
    }
    await Future.delayed(const Duration(milliseconds: 1200));
    if (mounted) setState(() => _done = true);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF09080A),
      body: Stack(
        fit: StackFit.expand,
        children: [
          // REQ 3: aura.mp4 for intro
          if (widget.videoCtrl?.value.isInitialized == true)
            FittedBox(
              fit: BoxFit.cover,
              child: SizedBox(
                width: widget.videoCtrl!.value.size.width,
                height: widget.videoCtrl!.value.size.height,
                child: VideoPlayer(widget.videoCtrl!),
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
          Positioned(
            top: 24,
            left: 0,
            right: 0,
            child: Center(
              child: Image.asset(
                'assets/aura_icon_haze.png',
                width: 280,
                height: 280,
                fit: BoxFit.contain,
              ),
            ),
          ),
          SafeArea(
            child: Align(
              alignment: Alignment.bottomCenter,
              child: ClipRRect(
                borderRadius: const BorderRadius.vertical(
                  top: Radius.circular(34),
                ),
                child: BackdropFilter(
                  filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
                  child: Container(
                    width: double.infinity,
                    constraints: BoxConstraints(
                      minHeight: MediaQuery.of(context).size.height * 0.72,
                    ),
                    decoration: BoxDecoration(
                      color: Colors.black.withOpacity(0.16),
                      border: Border(
                        top: BorderSide(
                          color: Colors.white.withOpacity(0.16),
                          width: 1,
                        ),
                      ),
                    ),
                    child: SingleChildScrollView(
                      padding: const EdgeInsets.fromLTRB(24, 22, 24, 26),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              GestureDetector(
                                onTap: widget.onBack,
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
                              const Spacer(),
                              Container(
                                width: 44,
                                height: 4,
                                decoration: BoxDecoration(
                                  color: Colors.white.withOpacity(0.28),
                                  borderRadius: BorderRadius.circular(999),
                                ),
                              ),
                              const Spacer(),
                              const SizedBox(width: 34),
                            ],
                          ),
                          const SizedBox(height: 40),
                          for (int i = 0; i < _lines.length; i++)
                            Padding(
                              padding: const EdgeInsets.only(bottom: 20),
                              child: BlurRevealText(
                                text: _lines[i],
                                style:
                                    i == 0
                                        ? _f(
                                          AuraTheme.textPrimary,
                                          size: 30,
                                          weight: FontWeight.w700,
                                          height: 1.25,
                                        )
                                        : _f(
                                          AuraTheme.textSecondary,
                                          size: 16,
                                          weight: FontWeight.w400,
                                          height: 1.45,
                                        ),
                                revealProgress: i < _visibleLines ? 1.0 : 0.0,
                                duration: const Duration(milliseconds: 900),
                              ),
                            ),
                          const SizedBox(height: 50),
                          AnimatedOpacity(
                            opacity: _done ? 1.0 : 0.0,
                            duration: const Duration(milliseconds: 700),
                            child: AuraButton(
                              label: "Let's get started →",
                              isPrimary: false,
                              onTap: _done ? widget.onComplete : null,
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
        ],
      ),
    );
  }
}

// ── Voice Question Flow ────────────────────────────────────────────────────

class _VoiceQuestionFlow extends StatefulWidget {
  final VideoPlayerController? videoCtrl;
  final AnimationController waveCtrl;
  final VoidCallback onBack;
  final void Function(String language, Map<String, String> answers) onComplete;
  final Future<void> Function(String) onSpeak;

  const _VoiceQuestionFlow({
    required this.videoCtrl,
    required this.waveCtrl,
    required this.onBack,
    required this.onComplete,
    required this.onSpeak,
  });

  @override
  State<_VoiceQuestionFlow> createState() => _VoiceQuestionFlowState();
}

class _VoiceQuestionFlowState extends State<_VoiceQuestionFlow> {
  int _step = -1; // -1 = language
  String _language = 'en';
  final Map<String, String> _answers = {
    'name': '',
    'job': '',
    'accessibility': '',
    'tasks': '',
  };
  final List<String> _keys = ['name', 'job', 'accessibility', 'tasks'];

  final List<String> _englishQ = [
    "What's your name?",
    "What do you do for work?",
    "Any accessibility preferences I should know about?",
    "What tasks do you most want AURA to help with?",
  ];
  final List<String> _arabicQ = [
    "ما اسمك؟",
    "ما هو عملك؟",
    "هل هناك أي تفضيلات متعلقة بإمكانية الوصول؟",
    "ما نوع المهام التي ترغب في أن تساعدك AURA فيها؟",
  ];

  List<String> get _currentQ => _language == 'ar' ? _arabicQ : _englishQ;

  final TextEditingController _ctrl = TextEditingController();

  // REQ 7: STT — auto listening
  late stt.SpeechToText _speech;
  bool _isListening = false;
  bool _speechAvailable = false;
  bool _autoListen = false;
  bool _speechInitInProgress = false;
  static final RegExp _navCommands = RegExp(
    r'\b(next|done|continue|go|submit|ok|okay)\b|التالي|تالي|تم|اكمل|استمر|كمل',
    caseSensitive: false,
  );

  @override
  void initState() {
    super.initState();
    _speech = stt.SpeechToText();
    _ensureSpeechReady();
  }

  Future<void> _initSpeech() async {
    _speechAvailable = await _speech.initialize(
      onStatus: (s) {
        if (s == 'done' || s == 'notListening') {
          if (mounted) setState(() => _isListening = false);
          if (_autoListen && mounted && _step >= 0 && _step < _keys.length) {
            Future.delayed(const Duration(milliseconds: 240), _startListening);
          }
        }
      },
      onError: (_) {
        if (mounted) setState(() => _isListening = false);
        if (_autoListen && mounted && _step >= 0 && _step < _keys.length) {
          Future.delayed(const Duration(milliseconds: 500), _startListening);
        }
      },
      debugLogging: false,
    );
  }

  Future<void> _ensureSpeechReady() async {
    if (_speechInitInProgress) return;
    _speechInitInProgress = true;
    try {
      final status = await Permission.microphone.request();
      if (!status.isGranted) {
        if (mounted) setState(() => _speechAvailable = false);
        return;
      }
      await _initSpeech();
    } finally {
      _speechInitInProgress = false;
    }
  }

  Future<void> _startListening() async {
    if (_isListening) return;
    if (!_speechAvailable) {
      await _ensureSpeechReady();
    }
    if (!_speechAvailable) return;
    _autoListen = true;
    await _speech.listen(
      onResult: (val) {
        if (mounted) {
          final raw = val.recognizedWords;
          final cleaned = raw.replaceAll(_navCommands, '').trim();
          setState(() => _ctrl.text = cleaned);
          if (_navCommands.hasMatch(raw) && !_isHandlingAdvance) {
            _isHandlingAdvance = true;
            _stopListening(manual: true);
            Future.delayed(const Duration(milliseconds: 260), () {
              _next();
              _isHandlingAdvance = false;
            });
            return;
          }
        }
      },
      localeId: _language == 'ar' ? 'ar_SA' : 'en_US',
      listenMode: stt.ListenMode.dictation,
      partialResults: true,
      cancelOnError: false,
      listenFor: const Duration(minutes: 5),
      pauseFor: const Duration(seconds: 15),
    );
    if (mounted) setState(() => _isListening = true);
  }

  bool _isHandlingAdvance = false;

  void _stopListening({bool manual = false}) {
    if (manual) _autoListen = false;
    _speech.stop();
    if (mounted) setState(() => _isListening = false);
  }

  void _setLanguage(String lang) async {
    setState(() {
      _language = lang;
      _step = 0;
    });
    await widget.onSpeak(_currentQ[0]);
    Future.delayed(const Duration(milliseconds: 600), _startListening);
  }

  void _next() async {
    if (_step >= 0 && _step < _keys.length) {
      _answers[_keys[_step]] = _ctrl.text.trim();
    }
    _ctrl.clear();
    _stopListening();

    if (_step + 1 >= _keys.length) {
      _autoListen = false;
      widget.onComplete(_language, Map.from(_answers));
    } else {
      setState(() => _step++);
      await widget.onSpeak(_currentQ[_step]);
      Future.delayed(const Duration(milliseconds: 600), _startListening);
    }
  }

  @override
  void dispose() {
    _autoListen = false;
    _speech.stop();
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AuraTheme.bgBase,
      body: Stack(
        fit: StackFit.expand,
        children: [
          // REQ 3: aura_calm.webm for question steps
          if (widget.videoCtrl?.value.isInitialized == true)
            FittedBox(
              fit: BoxFit.cover,
              child: SizedBox(
                width: widget.videoCtrl!.value.size.width,
                height: widget.videoCtrl!.value.size.height,
                child: VideoPlayer(widget.videoCtrl!),
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
          Positioned(
            top: 20,
            left: 0,
            right: 0,
            child: Center(
              child: Image.asset(
                'assets/aura_icon_haze.png',
                width: 250,
                height: 250,
                fit: BoxFit.contain,
              ),
            ),
          ),
          SafeArea(
            child: Align(
              alignment: Alignment.bottomCenter,
              child: ClipRRect(
                borderRadius: const BorderRadius.vertical(
                  top: Radius.circular(34),
                ),
                child: BackdropFilter(
                  filter: ImageFilter.blur(sigmaX: 18, sigmaY: 18),
                  child: Container(
                    width: double.infinity,
                    constraints: BoxConstraints(
                      minHeight: MediaQuery.of(context).size.height * 0.76,
                    ),
                    decoration: BoxDecoration(
                      color: Colors.black.withOpacity(0.17),
                      border: Border(
                        top: BorderSide(
                          color: Colors.white.withOpacity(0.16),
                          width: 1,
                        ),
                      ),
                    ),
                    child: Column(
                      children: [
                        Padding(
                          padding: const EdgeInsets.fromLTRB(16, 14, 16, 0),
                          child: Row(
                            children: [
                              GestureDetector(
                                onTap: widget.onBack,
                                child: Container(
                                  padding: const EdgeInsets.all(8),
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
                              const Spacer(),
                              Container(
                                width: 44,
                                height: 4,
                                decoration: BoxDecoration(
                                  color: Colors.white.withOpacity(0.28),
                                  borderRadius: BorderRadius.circular(999),
                                ),
                              ),
                              const Spacer(),
                              const SizedBox(width: 34),
                            ],
                          ),
                        ),
                        Expanded(
                          child: AnimatedSwitcher(
                            duration: const Duration(milliseconds: 450),
                            transitionBuilder:
                                (child, anim) =>
                                    FadeTransition(opacity: anim, child: child),
                            child:
                                _step == -1
                                    ? _buildLangSelect()
                                    : _buildQuestion(),
                          ),
                        ),
                      ],
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

  Widget _buildLangSelect() {
    return Center(
      key: const ValueKey('lang'),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(32, 20, 32, 10),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            BlurRevealText(
              text: 'Select your language\nاختر لغتك',
              style: _f(
                AuraTheme.textPrimary,
                size: 28,
                weight: FontWeight.w600,
                height: 1.4,
              ),
              revealProgress: 1.0,
              duration: const Duration(milliseconds: 1400),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 48),
            Row(
              children: [
                Expanded(
                  child: _LangButton(
                    label: 'English',
                    onTap: () => _setLanguage('en'),
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: _LangButton(
                    label: 'العربية',
                    onTap: () => _setLanguage('ar'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildQuestion() {
    final idx = _step.clamp(0, _currentQ.length - 1);
    final isAr = _language == 'ar';
    return Column(
      key: ValueKey('q_$_step'),
      children: [
        // Progress bar
        Padding(
          padding: const EdgeInsets.fromLTRB(24, 20, 24, 0),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: (_step + 1) / _keys.length,
              backgroundColor: Colors.white.withOpacity(0.08),
              valueColor: const AlwaysStoppedAnimation(AuraTheme.pink400),
              minHeight: 3,
            ),
          ),
        ),
        Expanded(
          child: Center(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 560),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Align(
                      alignment:
                          isAr ? Alignment.centerRight : Alignment.centerLeft,
                      child: AnimatedQuestionText(
                        text: _currentQ[idx],
                        isArabic: isAr,
                      ),
                    ),
                    const SizedBox(height: 50),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.center,
                      children: [
                        Expanded(
                          child: _GlassTextField(
                            controller: _ctrl,
                            hint: isAr ? 'تحدث أو اكتب...' : 'Speak or type...',
                            isArabic: isAr,
                            onSubmitted: (_) => _next(),
                          ),
                        ),
                        const SizedBox(width: 10),
                        _desktopMicWidget(),
                        const SizedBox(width: 8),
                        GestureDetector(
                          onTap: _next,
                          child: Container(
                            width: 52,
                            height: 52,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: Colors.white.withOpacity(0.08),
                              border: Border.all(
                                color: AuraTheme.pink400.withOpacity(0.45),
                              ),
                            ),
                            child: Icon(
                              _step < _keys.length - 1
                                  ? Icons.arrow_forward_rounded
                                  : Icons.check_rounded,
                              color: AuraTheme.pink300,
                              size: 22,
                            ),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    Text(
                      isAr
                          ? 'قل: التالي أو تم للمتابعة'
                          : 'Say: next or done to continue',
                      style: _f(AuraTheme.textMuted, size: 12),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      _isListening
                          ? (isAr ? 'جاري الاستماع...' : 'Listening...')
                          : (isAr
                              ? 'اضغط الميكروفون لإعادة التفعيل'
                              : 'Tap mic to re-enable'),
                      style: _f(
                        _isListening ? AuraTheme.pink300 : AuraTheme.textMuted,
                        size: 11,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }

  Widget _desktopMicWidget() {
    return GestureDetector(
      onTap:
          _isListening ? () => _stopListening(manual: true) : _startListening,
      child: AnimatedBuilder(
        animation: widget.waveCtrl,
        builder: (_, __) {
          final t = widget.waveCtrl.value;
          final pulse = _isListening ? (0.95 + sin(t * pi * 2) * 0.08) : 1.0;
          return Transform.scale(
            scale: pulse,
            child: Container(
              width: 54,
              height: 54,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color:
                    _isListening
                        ? AuraTheme.pink400.withOpacity(0.18)
                        : Colors.white.withOpacity(0.08),
                border: Border.all(
                  color:
                      _isListening
                          ? AuraTheme.pink400.withOpacity(0.7)
                          : Colors.white.withOpacity(0.2),
                ),
                boxShadow:
                    _isListening
                        ? [
                          BoxShadow(
                            color: AuraTheme.pink500.withOpacity(0.35),
                            blurRadius: 16,
                            spreadRadius: 1,
                          ),
                        ]
                        : null,
              ),
              child: Icon(
                _isListening ? Icons.mic_rounded : Icons.mic_none_rounded,
                color: _isListening ? AuraTheme.pink300 : Colors.white,
                size: 24,
              ),
            ),
          );
        },
      ),
    );
  }
}

// ── Shared UI Components ──────────────────────────────────────────────────

class _PulseFaceButton extends StatefulWidget {
  @override
  State<_PulseFaceButton> createState() => _PulseFaceButtonState();
}

class _PulseFaceButtonState extends State<_PulseFaceButton>
    with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;
  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1800),
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _ctrl,
      builder:
          (_, __) => Container(
            padding: const EdgeInsets.all(44),
            decoration: BoxDecoration(
              shape: BoxShape.rectangle,
              borderRadius: BorderRadius.circular(50),
              color: const Color.fromARGB(255, 57, 57, 57).withOpacity(0.08),
              border: Border.all(
                color: const Color.fromARGB(
                  255,
                  193,
                  193,
                  193,
                ).withOpacity(0.08),
                width: 1.5,
              ),
              boxShadow: [
                BoxShadow(
                  color: const Color.fromARGB(
                    255,
                    158,
                    158,
                    158,
                  ).withOpacity(0.1 + _ctrl.value * 0.15),
                  blurRadius: 24 + _ctrl.value * 16,
                  spreadRadius: 4,
                ),
              ],
            ),
            child: Image.asset(
              'assets/face_id_icon_transparent.png',
              width: 210,
              height: 210,
              fit: BoxFit.contain,
            ),
          ),
    );
  }
}

class _LangButton extends StatelessWidget {
  final String label;
  final VoidCallback onTap;
  const _LangButton({required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(18),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
          child: Container(
            height: 62,
            decoration: BoxDecoration(
              color: Colors.white.withOpacity(0.08),
              borderRadius: BorderRadius.circular(18),
              border: Border.all(color: Colors.white.withOpacity(0.18)),
            ),
            alignment: Alignment.center,
            child: Text(
              label,
              style: const TextStyle(
                fontFamily: 'PlusJakartaSans',
                color: Colors.white,
                fontSize: 17,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _GlassTextField extends StatelessWidget {
  final TextEditingController controller;
  final String hint;
  final bool isArabic;
  final bool isValid;
  final bool hasError;
  final ValueChanged<String>? onChanged;
  final ValueChanged<String>? onSubmitted;
  final Widget? suffix;

  const _GlassTextField({
    required this.controller,
    required this.hint,
    this.isArabic = false,
    this.isValid = false,
    this.hasError = false,
    this.onChanged,
    this.onSubmitted,
    this.suffix,
  });

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(18),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 14, sigmaY: 14),
        child: Container(
          decoration: BoxDecoration(
            color: Colors.white.withOpacity(0.05),
            borderRadius: BorderRadius.circular(18),
            border: Border.all(
              color:
                  isValid
                      ? AuraTheme.success.withOpacity(0.45)
                      : hasError
                      ? AuraTheme.error.withOpacity(0.45)
                      : Colors.white.withOpacity(0.12),
            ),
          ),
          child: TextField(
            controller: controller,
            style: const TextStyle(
              fontFamily: 'PlusJakartaSans',
              color: Colors.white,
              fontSize: 15,
            ),
            textDirection: isArabic ? TextDirection.rtl : TextDirection.ltr,
            onChanged: onChanged,
            onSubmitted: onSubmitted,
            decoration: InputDecoration(
              hintText: hint,
              hintStyle: TextStyle(
                fontFamily: 'PlusJakartaSans',
                color: Colors.white.withOpacity(0.35),
                fontSize: 14,
              ),
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 18,
                vertical: 14,
              ),
              border: InputBorder.none,
              suffixIcon: suffix,
            ),
          ),
        ),
      ),
    );
  }
}

// ── Blur reveal + animated question text ─────────────────────────────────

class BlurRevealText extends ImplicitlyAnimatedWidget {
  final String text;
  final TextStyle style;
  final double revealProgress;
  final TextAlign textAlign;
  final TextDirection? textDirection;

  const BlurRevealText({
    super.key,
    required this.text,
    required this.style,
    required this.revealProgress,
    this.textAlign = TextAlign.left,
    this.textDirection,
    Duration duration = const Duration(milliseconds: 1000),
    Curve curve = Curves.easeOut,
  }) : super(duration: duration, curve: curve);

  @override
  AnimatedWidgetBaseState<BlurRevealText> createState() =>
      _BlurRevealTextState();
}

class _BlurRevealTextState extends AnimatedWidgetBaseState<BlurRevealText> {
  Tween<double>? _revealTween;

  @override
  void forEachTween(TweenVisitor<dynamic> visitor) {
    _revealTween =
        visitor(
              _revealTween,
              widget.revealProgress,
              (dynamic value) => Tween<double>(begin: value as double),
            )
            as Tween<double>?;
  }

  @override
  Widget build(BuildContext context) {
    final progress = _revealTween?.evaluate(animation) ?? 0.0;
    final opacity = progress.clamp(0.0, 1.0);
    final blurAmount = (1.0 - progress) * 10.0;

    return Opacity(
      opacity: opacity,
      child: ImageFiltered(
        imageFilter: ImageFilter.blur(
          sigmaX: blurAmount.clamp(0.001, 20.0),
          sigmaY: blurAmount.clamp(0.001, 20.0),
        ),
        child: Text(
          widget.text,
          style: widget.style,
          textAlign: widget.textAlign,
          textDirection: widget.textDirection,
        ),
      ),
    );
  }
}

class AnimatedQuestionText extends StatefulWidget {
  final String text;
  final bool isArabic;
  const AnimatedQuestionText({
    super.key,
    required this.text,
    this.isArabic = false,
  });

  @override
  State<AnimatedQuestionText> createState() => _AnimatedQuestionTextState();
}

class _AnimatedQuestionTextState extends State<AnimatedQuestionText> {
  late String _source;
  int _visibleChars = 0;

  @override
  void initState() {
    super.initState();
    _source = widget.text;
    _startAnimation();
  }

  @override
  void didUpdateWidget(covariant AnimatedQuestionText oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.text != widget.text) {
      _source = widget.text;
      setState(() => _visibleChars = 0);
      _startAnimation();
    }
  }

  void _startAnimation() {
    Future.doWhile(() async {
      await Future.delayed(const Duration(milliseconds: 32));
      if (!mounted) return false;
      if (_visibleChars < _source.length) {
        setState(() => _visibleChars++);
        return true;
      }
      return false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: widget.isArabic ? TextDirection.rtl : TextDirection.ltr,
      child: Text(
        _source.substring(0, _visibleChars.clamp(0, _source.length)),
        style: _f(
          AuraTheme.textPrimary,
          size: 30,
          weight: FontWeight.w600,
          height: 1.35,
        ),
      ),
    );
  }
}

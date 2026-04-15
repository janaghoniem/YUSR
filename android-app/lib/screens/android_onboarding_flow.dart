// lib/screens/android_onboarding_flow.dart
import 'package:flutter/material.dart';
import '../theme.dart';
import '../services/auth_service.dart';
import '../services/session_store.dart';
import 'face_scan_screen.dart';

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

/// Full onboarding flow for new Android users.
/// Step 0: questions (reuses existing OnboardingScreen internals via callbacks)
/// Step 1: username input
/// Step 2: face registration
/// Step 3: account creation + memory storage
class AndroidOnboardingFlow extends StatefulWidget {
  final void Function(
      String userId, String username, String sessionId, String language)
      onComplete;

  const AndroidOnboardingFlow({super.key, required this.onComplete});

  @override
  State<AndroidOnboardingFlow> createState() => _AndroidOnboardingFlowState();
}

class _AndroidOnboardingFlowState extends State<AndroidOnboardingFlow> {
  // Onboarding answers collected from the 4 questions
  // These are populated via the embedded question flow
  String _language = 'en';
  final Map<String, String> _answers = {
    'name': '',
    'job': '',
    'accessibility': '',
    'tasks': '',
  };

  // Which high-level step we are in
  // 0 = questions, 1 = username, 2 = face, 3 = creating account
  int _flowStep = 0;

  final TextEditingController _usernameCtrl = TextEditingController();
  bool _usernameChecking = false;
  bool? _usernameAvailable;
  String? _usernameError;

  String? _faceBase64;
  bool _creatingAccount = false;
  String? _creationError;

  String _userId = '';

  @override
  void initState() {
    super.initState();
    _userId = AuthService.generateUserId();
  }

  @override
  void dispose() {
    _usernameCtrl.dispose();
    super.dispose();
  }

  // ── Step 0: Questions ──────────────────────────────────────────────────────

  // The existing OnboardingScreen handles the 4 questions and calls _continue()
  // when done. We embed a wrapper that collects answers through text fields
  // shown one at a time, maintaining the same animated word-reveal Android UI.

  Widget _buildQuestionsStep() {
    // We directly call the existing OnboardingScreen but intercept its navigation.
    // Since OnboardingScreen does `Navigator.of(context).pushReplacement(HomeWrapper)`,
    // we wrap it in a Navigator scope so the replacement is caught locally.
    return _EmbeddedQuestionFlow(
      onComplete: (String language, Map<String, String> answers) {

        setState(() {
          _language = language;
          _answers.addAll(answers);
          // Pre-fill username field with the name from step 0
          // (user can still edit it; username availability is re-checked)
        final nameFromAnswers = answers['name'] ?? '';
          if (nameFromAnswers.isNotEmpty && _usernameCtrl.text.isEmpty) {
            _usernameCtrl.text = nameFromAnswers;
            // Mark as valid immediately — no uniqueness check
            Future.delayed(const Duration(milliseconds: 100), () {
              if (mounted) _checkUsername(_usernameCtrl.text);
            });
          }
          _flowStep = 1;
        });
      },
    );
  }

  // ── Step 1: Username ───────────────────────────────────────────────────────

  // Name field — no uniqueness check needed.
  // Identity is guaranteed by the server-generated userId.
  void _checkUsername(String val) {
    setState(() {
      _usernameAvailable = val.trim().length >= 2;
      _usernameError = null;
    });
  }

  Widget _buildUsernameStep() {
    return Scaffold(
      backgroundColor: AuraTheme.bgBase,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SizedBox(height: 20),
              Text(
                _answers['name']?.isNotEmpty == true
                    ? 'Almost there, ${_answers['name']}!'
                    : 'What should we call you?',
                style: _f(AuraTheme.textPrimary, size: 28, weight: FontWeight.w600),
              ),
              const SizedBox(height: 8),
              Text('You can edit this later.',
                  style: _f(AuraTheme.textSecondary, size: 14)),
              const SizedBox(height: 36),
              // Username input
              Container(
                decoration: BoxDecoration(
                  color: AuraTheme.bgElevated,
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(
                    color: _usernameAvailable == true
                        ? AuraTheme.success.withOpacity(0.5)
                        : _usernameError != null
                            ? AuraTheme.error.withOpacity(0.5)
                            : Colors.white.withOpacity(0.1),
                  ),
                ),
                child: TextField(
                  controller: _usernameCtrl,
                  style: _f(AuraTheme.textPrimary, size: 16),
                  decoration: InputDecoration(
                    hintText: 'e.g. sara',
                    hintStyle: _f(AuraTheme.textDisabled, size: 15),
                    contentPadding: const EdgeInsets.symmetric(
                        horizontal: 20, vertical: 16),
                    border: InputBorder.none,
                    suffixIcon: _usernameChecking
                        ? const Padding(
                            padding: EdgeInsets.all(14),
                            child: SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                    color: AuraTheme.pink400)))
                        : _usernameAvailable == true
                            ? const Icon(Icons.check_circle_rounded,
                                color: AuraTheme.success)
                            : null,
                  ),
                  onChanged: (val) {
                    _checkUsername(val);
                  },
                ),
              ),
              if (_usernameError != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8, left: 4),
                  child: Text(_usernameError!,
                      style: _f(AuraTheme.error, size: 12)),
                ),
              if (_usernameAvailable == true)
                Padding(
                  padding: const EdgeInsets.only(top: 8, left: 4),
                  child: Text('✓ Looks good',
                      style: _f(AuraTheme.success, size: 12,
                          weight: FontWeight.w600)),
                ),
              const Spacer(),
              GestureDetector(
              onTap: (_usernameCtrl.text.trim().length >= 2)
                    ? () => setState(() => _flowStep = 2)
                    : null,
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(vertical: 18),
                  decoration: BoxDecoration(
                  gradient: (_usernameCtrl.text.trim().length >= 2)
                        ? const LinearGradient(
                            colors: [AuraTheme.pink500, AuraTheme.pink700])
                        : null,
                    color: (_usernameCtrl.text.trim().length < 2)
                        ? AuraTheme.bgMuted
                        : null,
                    borderRadius: BorderRadius.circular(30),
                  ),
                  alignment: Alignment.center,
                  child: Text('Continue →',
                      style: _f(
                          (_usernameCtrl.text.trim().length >= 2)
                              ? Colors.white
                              : AuraTheme.textDisabled,
                          size: 15,
                          weight: FontWeight.w700)),
                ),
              ),
              const SizedBox(height: 16),
            ],
          ),
        ),
      ),
    );
  }

  // ── Step 2: Face Registration ──────────────────────────────────────────────

  Widget _buildFaceStep() {
    return Scaffold(
      backgroundColor: AuraTheme.bgBase,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SizedBox(height: 20),
              Text(
                'Register your face, ${_usernameCtrl.text.isNotEmpty ? _usernameCtrl.text : 'there'}!',
                style: _f(AuraTheme.textPrimary, size: 28, weight: FontWeight.w600),
              ),
              const SizedBox(height: 8),
              Text(
                  'You\'ll use your face to log in. No password needed.',
                  style: _f(AuraTheme.textSecondary, size: 14)),
              const Spacer(),
              if (_faceBase64 != null)
                Center(
                  child: Column(
                    children: [
                      Container(
                        width: 80,
                        height: 80,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: AuraTheme.success.withOpacity(0.15),
                          border: Border.all(
                              color: AuraTheme.success, width: 2),
                        ),
                        child: const Icon(Icons.check_rounded,
                            color: AuraTheme.success, size: 40),
                      ),
                      const SizedBox(height: 16),
                      Text('Face captured!',
                          style: _f(AuraTheme.success,
                              size: 16, weight: FontWeight.w600)),
                      const SizedBox(height: 8),
                      GestureDetector(
                        onTap: () async {
                          final img =
                              await Navigator.of(context).push<String?>(
                            MaterialPageRoute(
                              builder: (_) => const FaceScanScreen(
                                title: 'Re-scan',
                                subtitle: 'Retake your face scan',
                              ),
                            ),
                          );
                          if (img != null && mounted) {
                            setState(() => _faceBase64 = img);
                          }
                        },
                        child: Text('Retake',
                            style: _f(AuraTheme.textMuted, size: 13)),
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
                          builder: (_) => const FaceScanScreen(
                            title: 'Register Face',
                            subtitle: 'Look directly at the camera',
                          ),
                        ),
                      );
                      if (img != null && mounted) {
                        setState(() => _faceBase64 = img);
                      }
                    },
                    child: Container(
                      padding: const EdgeInsets.all(40),
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: AuraTheme.pink500.withOpacity(0.12),
                        border: Border.all(
                            color: AuraTheme.pink400.withOpacity(0.4),
                            width: 2),
                      ),
                      child: const Icon(
                          Icons.face_retouching_natural_rounded,
                          color: AuraTheme.pink400,
                          size: 56),
                    ),
                  ),
                ),
              const Spacer(),
              if (_creationError != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Text(_creationError!,
                      style: _f(AuraTheme.error, size: 13),
                      textAlign: TextAlign.center),
                ),
              GestureDetector(
                onTap: (_faceBase64 != null && !_creatingAccount)
                    ? _createAccount
                    : null,
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(vertical: 18),
                  decoration: BoxDecoration(
                    gradient: (_faceBase64 != null)
                        ? const LinearGradient(
                            colors: [AuraTheme.pink500, AuraTheme.pink700])
                        : null,
                    color:
                        (_faceBase64 == null) ? AuraTheme.bgMuted : null,
                    borderRadius: BorderRadius.circular(30),
                  ),
                  alignment: Alignment.center,
                  child: _creatingAccount
                      ? const SizedBox(
                          height: 22,
                          width: 22,
                          child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.white))
                      : Text('Create Account →',
                          style: _f(
                              _faceBase64 != null
                                  ? Colors.white
                                  : AuraTheme.textDisabled,
                              size: 15,
                              weight: FontWeight.w700)),
                ),
              ),
              const SizedBox(height: 24),
            ],
          ),
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

    try {
      final username = _usernameCtrl.text.trim();
      final introduction = _answers.values
          .where((v) => v.isNotEmpty)
          .join('. ');

      // 1. Register face
      await AuthService.registerFace(
        userId: _userId,
        username: username,
        faceImageBase64: _faceBase64!,
      );

      // 2. Create account record
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

      // 3. Store onboarding answers in Mem0
      await AuthService.storeIntroduction(
        userId: _userId,
        language: _language,
        answers: _answers,
      );

      // 4. Create server-side session
      final sessionId = await AuthService.createSession(_userId);

      // 5. Persist to SharedPreferences
      await SessionStore.save(
        userId: _userId,
        username: username,
        sessionId: sessionId,
        language: _language,
      );

      if (mounted) {
        widget.onComplete(_userId, username, sessionId, _language);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _creatingAccount = false;
          _creationError = e.toString().replaceFirst('Exception: ', '');
        });
      }
    }
  }

  // ── Build ──────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 400),
      switchInCurve: Curves.easeOut,
      switchOutCurve: Curves.easeIn,
      transitionBuilder: (child, animation) =>
          FadeTransition(opacity: animation, child: child),
      child: switch (_flowStep) {
        0 => KeyedSubtree(
            key: const ValueKey('questions'),
            child: _buildQuestionsStep()),
        1 => KeyedSubtree(
            key: const ValueKey('username'),
            child: _buildUsernameStep()),
        _ => KeyedSubtree(
            key: const ValueKey('face'),
            child: _buildFaceStep()),
      },
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Embedded question flow
// Wraps the existing OnboardingScreen question logic without modifying it.
// Uses a local Navigator to intercept the pushReplacement to HomeWrapper.
// ─────────────────────────────────────────────────────────────────────────────

class _EmbeddedQuestionFlow extends StatelessWidget {
  final void Function(String language, Map<String, String> answers) onComplete;

  const _EmbeddedQuestionFlow({required this.onComplete});

  @override
  Widget build(BuildContext context) {
    // We use a Navigator that intercepts route pushes.
    // OnboardingScreen's _nextStep() calls Navigator.of(context).pushReplacement(HomeWrapper)
    // when all questions are done. We catch that push and call onComplete instead.
    return Navigator(
      onGenerateRoute: (settings) => MaterialPageRoute(
        builder: (_) => _InterceptingOnboarding(onComplete: onComplete),
      ),
    );
  }
}

/// Re-implements OnboardingScreen's logic purely as a data-collector.
/// The UI style is identical to OnboardingScreen (it imports the same theme).
/// The difference: instead of navigating to HomeWrapper, it calls onComplete
/// with the collected answers.
class _InterceptingOnboarding extends StatefulWidget {
  final void Function(String language, Map<String, String> answers) onComplete;
  const _InterceptingOnboarding({required this.onComplete});

  @override
  State<_InterceptingOnboarding> createState() =>
      _InterceptingOnboardingState();
}

class _InterceptingOnboardingState extends State<_InterceptingOnboarding> {
  // We import the full OnboardingScreen file to reuse its widget directly.
  // Since we cannot modify OnboardingScreen, we call it and watch for
  // when it would navigate away — we intercept by overriding the route generator.
  //
  // IMPLEMENTATION NOTE: The cleanest approach is to replicate the question
  // list and collect answers here, calling widget.onComplete when done.
  // This avoids any dependency on OnboardingScreen's private state.

  int _step = -1; // -1 = language
  String _language = 'en';
  final List<String> _questionKeys = [
    'name', 'job', 'accessibility', 'tasks'
  ];
  final Map<String, String> _answers = {
    'name': '', 'job': '', 'accessibility': '', 'tasks': ''
  };

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

  List<String> get _currentQ =>
      _language == 'ar' ? _arabicQ : _englishQ;

  final TextEditingController _ctrl = TextEditingController();

  void _setLanguage(String lang) {
    setState(() {
      _language = lang;
      _step = 0;
    });
  }

  void _next() {
    if (_step >= 0 && _step < _questionKeys.length) {
      _answers[_questionKeys[_step]] = _ctrl.text.trim();
    }
    _ctrl.clear();
    if (_step + 1 >= _questionKeys.length) {
      widget.onComplete(_language, Map.from(_answers));
    } else {
      setState(() => _step++);
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AuraTheme.bgBase,
      body: SafeArea(
        child: AnimatedSwitcher(
          duration: const Duration(milliseconds: 450),
          transitionBuilder: (child, anim) =>
              FadeTransition(opacity: anim, child: child),
          child: _step == -1
              ? _buildLangSelect()
              : _buildQuestion(),
        ),
      ),
    );
  }

  Widget _buildLangSelect() {
    return Center(
      key: const ValueKey('lang'),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('Select your language\nاختر لغتك',
                style: TextStyle(
                    fontFamily: 'PlusJakartaSans',
                    fontSize: 28,
                    fontWeight: FontWeight.w600,
                    color: AuraTheme.textPrimary,
                    height: 1.4),
                textAlign: TextAlign.center),
            const SizedBox(height: 48),
            Row(
              children: [
                Expanded(
                    child: _langBtn('English', () => _setLanguage('en'))),
                const SizedBox(width: 16),
                Expanded(
                    child: _langBtn('العربية', () => _setLanguage('ar'))),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _langBtn(String label, VoidCallback onTap) => GestureDetector(
        onTap: onTap,
        child: Container(
          height: 64,
          decoration: BoxDecoration(
            color: Colors.white.withOpacity(0.08),
            borderRadius: BorderRadius.circular(16),
            border:
                Border.all(color: Colors.white.withOpacity(0.2)),
          ),
          alignment: Alignment.center,
          child: Text(label,
              style: TextStyle(
                  fontFamily: 'PlusJakartaSans',
                  color: Colors.white,
                  fontSize: 18,
                  fontWeight: FontWeight.w600)),
        ),
      );

  Widget _buildQuestion() {
    final idx = _step.clamp(0, _currentQ.length - 1);
    return Column(
      key: ValueKey('q_$_step'),
      children: [
        // Progress bar
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 24, 28, 0),
          child: LinearProgressIndicator(
            value: (_step + 1) / _questionKeys.length,
            backgroundColor: AuraTheme.bgMuted,
            valueColor: const AlwaysStoppedAnimation(AuraTheme.pink400),
            borderRadius: BorderRadius.circular(4),
          ),
        ),
        Expanded(
          child: Padding(
            padding:
                const EdgeInsets.symmetric(horizontal: 32, vertical: 40),
            child: Align(
              alignment: _language == 'ar'
                  ? Alignment.centerRight
                  : Alignment.centerLeft,
              child: Text(
                _currentQ[idx],
                style: TextStyle(
                    fontFamily: 'PlusJakartaSans',
                    fontSize: 30,
                    fontWeight: FontWeight.w600,
                    color: AuraTheme.textPrimary,
                    height: 1.35),
                textDirection: _language == 'ar'
                    ? TextDirection.rtl
                    : TextDirection.ltr,
              ),
            ),
          ),
        ),
        // Input bar — same style as OnboardingScreen
        Container(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 28),
          decoration: BoxDecoration(
            color: AuraTheme.bgElevated.withOpacity(0.5),
            border: Border(
                top: BorderSide(color: Colors.white.withOpacity(0.08))),
          ),
          child: Row(
            children: [
              Expanded(
                child: Container(
                  decoration: BoxDecoration(
                    color: Colors.white.withOpacity(0.04),
                    borderRadius: BorderRadius.circular(24),
                    border: Border.all(
                        color: Colors.white.withOpacity(0.1)),
                  ),
                  child: TextField(
                    controller: _ctrl,
                    style: TextStyle(
                        fontFamily: 'PlusJakartaSans',
                        color: AuraTheme.textPrimary,
                        fontSize: 16),
                    textDirection: _language == 'ar'
                        ? TextDirection.rtl
                        : TextDirection.ltr,
                    decoration: InputDecoration(
                      hintText: _language == 'ar'
                          ? 'اكتب إجابتك...'
                          : 'Type your answer...',
                      hintStyle: TextStyle(
                          fontFamily: 'PlusJakartaSans',
                          color: AuraTheme.textDisabled,
                          fontSize: 14),
                      contentPadding: const EdgeInsets.symmetric(
                          horizontal: 20, vertical: 14),
                      border: InputBorder.none,
                    ),
                    onSubmitted: (_) => _next(),
                  ),
                ),
              ),
              const SizedBox(width: 10),
              GestureDetector(
                onTap: _next,
                child: Container(
                  width: 48,
                  height: 48,
                  decoration: BoxDecoration(
                    gradient: const LinearGradient(
                        colors: [AuraTheme.pink500, AuraTheme.pink700]),
                    shape: BoxShape.circle,
                  ),
                  child: Icon(
                    _step < _questionKeys.length - 1
                        ? Icons.arrow_forward_rounded
                        : Icons.check_rounded,
                    color: Colors.white,
                    size: 22,
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
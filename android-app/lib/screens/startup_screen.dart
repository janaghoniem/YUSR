// lib/screens/startup_screen.dart
import 'package:flutter/material.dart';
import 'package:flutter/gestures.dart';
import 'package:video_player/video_player.dart';
import '../screens/android_login_screen.dart';
import '../screens/android_onboarding_flow.dart';
import '../main.dart';

class StartupScreen extends StatefulWidget {
  const StartupScreen({super.key});

  @override
  State<StartupScreen> createState() => _StartupScreenState();
}

class _StartupScreenState extends State<StartupScreen>
    with SingleTickerProviderStateMixin {
  late VideoPlayerController _controller;
  late TapGestureRecognizer _termsRecognizer;
  late TapGestureRecognizer _privacyRecognizer;
  late AnimationController _fadeCtrl;
  late Animation<double> _fadeAnim;

  @override
  void initState() {
    super.initState();
    _termsRecognizer = TapGestureRecognizer()..onTap = _showTerms;
    _privacyRecognizer = TapGestureRecognizer()..onTap = _showPrivacy;

    _fadeCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1600),
    );
    _fadeAnim = CurvedAnimation(
      parent: _fadeCtrl,
      curve: const Interval(0.0, 1.0, curve: Curves.easeOut),
    );

    // REQ 3: aura.mp4 for startup/login
    _controller = VideoPlayerController.asset('assets/aura.mp4')
      ..setLooping(true)
      ..initialize().then((_) {
        setState(() {});
        _controller.play();
        _fadeCtrl.forward();
      });
  }

  @override
  void dispose() {
    _termsRecognizer.dispose();
    _privacyRecognizer.dispose();
    _controller.dispose();
    _fadeCtrl.dispose();
    super.dispose();
  }

  void _goToLogin() {
    final nav = Navigator.of(context);
    nav.pushReplacement(PageRouteBuilder(
      pageBuilder: (_, __, ___) => AndroidLoginScreen(
        onLoginSuccess: (userId, username, sessionId, language) {
          nav.pushReplacement(PageRouteBuilder(
            pageBuilder: (_, __, ___) => _AuthedHomeWrapper(
              userId: userId,
              username: username,
              sessionId: sessionId,
              language: language,
            ),
            transitionsBuilder: (_, a, __, child) =>
                FadeTransition(opacity: a, child: child),
            transitionDuration: const Duration(milliseconds: 500),
          ));
        },
      ),
      transitionsBuilder: (_, a, __, child) =>
          FadeTransition(opacity: a, child: child),
      transitionDuration: const Duration(milliseconds: 500),
    ));
  }

  void _goToOnboarding() {
    final nav = Navigator.of(context);
    nav.pushReplacement(PageRouteBuilder(
      pageBuilder: (_, __, ___) => AndroidOnboardingFlow(
        onComplete: (userId, username, sessionId, language) {
          nav.pushReplacement(PageRouteBuilder(
            pageBuilder: (_, __, ___) => _AuthedHomeWrapper(
              userId: userId,
              username: username,
              sessionId: sessionId,
              language: language,
            ),
            transitionsBuilder: (_, a, __, child) =>
                FadeTransition(opacity: a, child: child),
            transitionDuration: const Duration(milliseconds: 500),
          ));
        },
      ),
      transitionsBuilder: (_, a, __, child) =>
          FadeTransition(opacity: a, child: child),
      transitionDuration: const Duration(milliseconds: 500),
    ));
  }

  void _showTerms() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => _buildBottomSheet(
        title: "Terms of Service",
        content:
            "Welcome to AURA (Autonomous Understanding & Reasoning Agents).\n\n"
            "1. Acceptance of Terms\nBy accessing and using AURA, you agree to be bound by these terms.\n\n"
            "2. Service Description\nAURA provides AI-powered autonomous multi-agent assistance. Because AURA utilizes generative AI, results are computational inferences and should be independently verified by the user. Responses do not constitute professional advice.\n\n"
            "3. User Responsibilities\nYou agree not to use AURA for unlawful activities, to harm others, or to generate malicious code, spam, or disruptive content. You must use AI-generated insights responsibly.\n\n"
            "4. Limitation of Liability\nThe creators and operators of AURA are not liable for any direct, indirect, or consequential damages arising from your reliance on the application or its agent-generated outputs.\n\n"
            "5. Changes to Service\nWe reserve the right to modify, suspend, or terminate the service at any time without prior notice as our autonomous agents and capabilities evolve.",
      ),
    );
  }

  void _showPrivacy() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => _buildBottomSheet(
        title: "Privacy Policy",
        content:
            "Your privacy is a core priority at AURA.\n\n"
            "1. Data Collection\nWe collect conversational data, text prompts, screen contexts, and related system states required for our autonomous agents to understand and assist you efficiently.\n\n"
            "2. Local & Cloud Processing\nTo ensure low-latency performance and privacy, AURA executes operations locally where possible. However, advanced reasoning tasks may securely share queries with cloud-based AI providers.\n\n"
            "3. Agent Memory\nAURA maintains persistent session memory of your preferences and interactions to improve context-awareness over time. You have full control and can clear this memory completely at any time in the settings.\n\n"
            "4. Data Sharing\nWe do not sell your personal data to third parties. Data shared with core LLM providers is heavily restricted and subject to strict enterprise policies that forbid using your prompts for public model training.\n\n"
            "5. Security\nWe implement strong encryption and standard security protocols to protect your interactions. However, we advise against passing highly sensitive secrets or passwords directly in plain-text prompts.",
      ),
    );
  }

  Widget _buildBottomSheet({required String title, required String content}) {
    return Container(
      height: MediaQuery.of(context).size.height * 0.70,
      decoration: const BoxDecoration(
        color: Color(0xFF111114),
        borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
      ),
      padding: const EdgeInsets.only(top: 12, left: 24, right: 24, bottom: 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Center(
            child: Container(
              width: 36,
              height: 3,
              margin: const EdgeInsets.only(bottom: 24),
              decoration: BoxDecoration(
                color: Colors.white24,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                title,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 20,
                  fontWeight: FontWeight.w600,
                  fontFamily: 'PlusJakartaSans',
                ),
              ),
              GestureDetector(
                onTap: () => Navigator.pop(context),
                child: Container(
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(
                    color: Colors.white.withOpacity(0.08),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: const Icon(Icons.close_rounded, color: Colors.white54, size: 18),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Expanded(
            child: SingleChildScrollView(
              child: Text(
                content,
                style: TextStyle(
                  color: Colors.white.withOpacity(0.75),
                  fontSize: 13.5,
                  fontFamily: 'PlusJakartaSans',
                  height: 1.7,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF09080A),
      body: Stack(
        fit: StackFit.expand,
        children: [
          // REQ 3: aura.mp4 background
          if (_controller.value.isInitialized)
            FittedBox(
              fit: BoxFit.cover,
              child: SizedBox(
                width: _controller.value.size.width,
                height: _controller.value.size.height,
                child: VideoPlayer(_controller),
              ),
            ),
          // Gradient overlay
          Container(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Colors.black.withOpacity(0.2),
                  Colors.black.withOpacity(0.55),
                ],
              ),
            ),
          ),

          SafeArea(
            child: FadeTransition(
              opacity: _fadeAnim,
              child: Column(
                children: [
                  // REQ 2: AURA title always at top, fixed alignment
                  Padding(
                    padding: const EdgeInsets.only(top: 44),
                    child: Column(
                      children: [
                        const Text(
                          'AURA',
                          style: TextStyle(
                            fontFamily: 'PlusJakartaSans',
                            fontSize: 36,
                            fontWeight: FontWeight.w500,
                            letterSpacing: 6,
                            color: Color(0xEEFFFFFF),
                          ),
                        ),
                        const SizedBox(height: 6),
                        Text(
                          'Your intelligent assistant',
                          style: TextStyle(
                            fontFamily: 'PlusJakartaSans',
                            fontSize: 13,
                            fontWeight: FontWeight.w400,
                            letterSpacing: 0.5,
                            color: Colors.white.withOpacity(0.55),
                          ),
                        ),
                      ],
                    ),
                  ),

                  const Spacer(),

                  // REQ 1: buttons — same size, sleek
                  Padding(
                    padding: const EdgeInsets.fromLTRB(28, 0, 28, 12),
                    child: Column(
                      children: [
                        // Sign In — primary filled
                        _PrimaryButton(
                          label: 'Sign In',
                          onTap: _goToLogin,
                        ),
                        const SizedBox(height: 12),
                        // Create Account — ghost outline
                        _OutlineButton(
                          label: 'Create Account',
                          onTap: _goToOnboarding,
                        ),
                        const SizedBox(height: 20),
                        // Legal text
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 16),
                          child: RichText(
                            textAlign: TextAlign.center,
                            text: TextSpan(
                              style: TextStyle(
                                fontFamily: 'PlusJakartaSans',
                                fontSize: 11,
                                height: 1.6,
                                fontWeight: FontWeight.w400,
                                color: Colors.white.withOpacity(0.45),
                              ),
                              children: [
                                const TextSpan(text: 'By continuing you agree to our '),
                                TextSpan(
                                  text: 'Terms',
                                  style: TextStyle(
                                    decoration: TextDecoration.underline,
                                    color: Colors.white.withOpacity(0.7),
                                  ),
                                  recognizer: _termsRecognizer,
                                ),
                                const TextSpan(text: ' and '),
                                TextSpan(
                                  text: 'Privacy Policy',
                                  style: TextStyle(
                                    decoration: TextDecoration.underline,
                                    color: Colors.white.withOpacity(0.7),
                                  ),
                                  recognizer: _privacyRecognizer,
                                ),
                                const TextSpan(text: '.'),
                              ],
                            ),
                          ),
                        ),
                        const SizedBox(height: 16),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// REQ 1: Primary button — same width/height as outline button
class _PrimaryButton extends StatelessWidget {
  final String label;
  final VoidCallback onTap;
  const _PrimaryButton({required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: double.infinity,
        height: 56,
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(28),
          boxShadow: [
            BoxShadow(
              color: Colors.white.withOpacity(0.15),
              blurRadius: 20,
              offset: const Offset(0, 6),
            ),
          ],
        ),
        alignment: Alignment.center,
        child: const Text(
          'Sign In',
          style: TextStyle(
            fontFamily: 'PlusJakartaSans',
            fontSize: 14,
            fontWeight: FontWeight.w700,
            letterSpacing: 1.0,
            color: Color(0xFF0D0B10),
          ),
        ),
      ),
    );
  }
}

// REQ 1: Outline button — exact same dimensions as primary
class _OutlineButton extends StatelessWidget {
  final String label;
  final VoidCallback onTap;
  const _OutlineButton({required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: double.infinity,
        height: 56,
        decoration: BoxDecoration(
          color: Colors.transparent,
          borderRadius: BorderRadius.circular(28),
          border: Border.all(
            color: Colors.white.withOpacity(0.4),
            width: 1.2,
          ),
        ),
        alignment: Alignment.center,
        child: Text(
          label,
          style: TextStyle(
            fontFamily: 'PlusJakartaSans',
            fontSize: 14,
            fontWeight: FontWeight.w600,
            letterSpacing: 1.0,
            color: Colors.white.withOpacity(0.85),
          ),
        ),
      ),
    );
  }
}

class _AuthedHomeWrapper extends StatelessWidget {
  final String userId;
  final String username;
  final String sessionId;
  final String language;

  const _AuthedHomeWrapper({
    required this.userId,
    required this.username,
    required this.sessionId,
    required this.language,
  });

  @override
  Widget build(BuildContext context) {
    return AutomationDemo(
      userId: userId,
      username: username,
      sessionId: sessionId,
      language: language,
    );
  }
}
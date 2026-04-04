// lib/screens/startup_screen.dart
import 'package:flutter/material.dart';
import 'package:flutter/gestures.dart';
import 'package:video_player/video_player.dart';
import 'onboarding_screen.dart';

class StartupScreen extends StatefulWidget {
  const StartupScreen({super.key});

  @override
  State<StartupScreen> createState() => _StartupScreenState();
}

class _StartupScreenState extends State<StartupScreen> {
  late VideoPlayerController _controller;
  late TapGestureRecognizer _termsRecognizer;
  late TapGestureRecognizer _privacyRecognizer;

  @override
  void initState() {
    super.initState();
    _termsRecognizer = TapGestureRecognizer()..onTap = _showTerms;
    _privacyRecognizer = TapGestureRecognizer()..onTap = _showPrivacy;

    _controller =
        VideoPlayerController.asset('assets/aura1.webm')
          ..setLooping(true)
          ..initialize().then((_) {
            setState(() {});
            _controller.play();
          });
  }

  @override
  void dispose() {
    _termsRecognizer.dispose();
    _privacyRecognizer.dispose();
    _controller.dispose();
    super.dispose();
  }

  void _continue() {
    Navigator.of(context).pushReplacement(
      PageRouteBuilder(
        pageBuilder: (_, __, ___) => const OnboardingScreen(),
        transitionsBuilder:
            (_, a, __, child) => FadeTransition(opacity: a, child: child),
        transitionDuration: const Duration(milliseconds: 650),
      ),
    );
  }

  void _showTerms() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder:
          (context) => _buildBottomSheet(
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
      builder:
          (context) => _buildBottomSheet(
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
        color: Color(0xFF141416),
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      padding: const EdgeInsets.only(top: 12, left: 24, right: 24, bottom: 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Drag handle
          Center(
            child: Container(
              width: 40,
              height: 4,
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
                  fontSize: 22,
                  fontWeight: FontWeight.w600,
                  fontFamily: 'PlusJakartaSans',
                ),
              ),
              IconButton(
                padding: EdgeInsets.zero,
                icon: const Icon(Icons.close_rounded, color: Colors.white54),
                onPressed: () => Navigator.pop(context),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Expanded(
            child: SingleChildScrollView(
              child: Text(
                content,
                style: TextStyle(
                  color: Colors.white.withOpacity(0.8),
                  fontSize: 14,
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
          // ── Video Background ─────────────────────────────────────────
          if (_controller.value.isInitialized)
            FittedBox(
              fit: BoxFit.cover,
              child: SizedBox(
                width: _controller.value.size.width,
                height: _controller.value.size.height,
                child: VideoPlayer(_controller),
              ),
            ),

          // ── AURA wordmark – top center ──────────────────────────────────
          SafeArea(
            child: Align(
              alignment: Alignment.topCenter,
              child: Padding(
                padding: const EdgeInsets.only(top: 52),
                child: TweenAnimationBuilder<double>(
                  tween: Tween(begin: 0.0, end: 1.0),
                  duration: const Duration(milliseconds: 1400),
                  curve: Curves.easeOut,
                  builder: (_, v, child) => Opacity(opacity: v, child: child),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Text(
                        'AURA',
                        style: TextStyle(
                          fontFamily: 'PlusJakartaSans',
                          fontSize: 34,
                          fontWeight: FontWeight.w500,
                          letterSpacing: 1.5,
                          color: Color(0xDDFFFFFF),
                        ),
                      ),
                      const SizedBox(height: 6),
                    ],
                  ),
                ),
              ),
            ),
          ),

          // ── Continue button & Terms of Service ──────────────────────────
          SafeArea(
            child: Align(
              alignment: Alignment.bottomCenter,
              child: Padding(
                padding: const EdgeInsets.only(bottom: 52),
                child: TweenAnimationBuilder<double>(
                  tween: Tween(begin: 0.0, end: 1.0),
                  duration: const Duration(milliseconds: 1600),
                  curve: const Interval(0.4, 1.0, curve: Curves.easeOut),
                  builder: (_, v, child) => Opacity(opacity: v, child: child),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      GestureDetector(
                        onTap: _continue,
                        child: const _GlassButton(label: 'Get Started'),
                      ),
                      const SizedBox(height: 16),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 40),
                        child: RichText(
                          textAlign: TextAlign.center,
                          text: TextSpan(
                            style: TextStyle(
                              fontFamily: 'PlusJakartaSans',
                              fontSize: 11,
                              height: 1.5,
                              fontWeight: FontWeight.w500,
                              letterSpacing: 0.5,
                              color: Colors.white.withOpacity(0.6),
                            ),
                            children: [
                              const TextSpan(
                                text:
                                    'By tapping "Get Started", you agree to our\n',
                              ),
                              TextSpan(
                                text: 'Terms of Service',
                                style: TextStyle(
                                  decoration: TextDecoration.underline,
                                  decorationColor: Colors.white.withOpacity(
                                    0.8,
                                  ),
                                  color: Colors.white.withOpacity(0.8),
                                  fontWeight: FontWeight.w600,
                                ),
                                recognizer: _termsRecognizer,
                              ),
                              const TextSpan(text: ' and '),
                              TextSpan(
                                text: 'Privacy Policy',
                                style: TextStyle(
                                  decoration: TextDecoration.underline,
                                  decorationColor: Colors.white.withOpacity(
                                    0.8,
                                  ),
                                  color: Colors.white.withOpacity(0.8),
                                  fontWeight: FontWeight.w600,
                                ),
                                recognizer: _privacyRecognizer,
                              ),
                              const TextSpan(text: '.'),
                            ],
                          ),
                        ),
                      ),
                    ],
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

class _GlassButton extends StatelessWidget {
  final String label;
  const _GlassButton({required this.label});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 110, vertical: 18),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(30),
        border: Border.all(color: Colors.white.withOpacity(0.18), width: 0.8),
      ),
      child: Text(
        label,
        style: const TextStyle(
          fontFamily: 'PlusJakartaSans',
          fontSize: 13,
          fontWeight: FontWeight.w800,
          letterSpacing: 1.5,
          color: Color.fromARGB(204, 0, 0, 0),
        ),
      ),
    );
  }
}

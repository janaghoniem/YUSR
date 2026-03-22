import 'package:flutter/material.dart';
import '../theme.dart';
import '../main.dart';
import '../services/accessibility_service.dart';
import '../widgets/cinematic_intro.dart';
import 'dart:async';

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  int _step = 0; // 0 = intro, 1 = name, 2 = role, 3 = language, etc

  final List<String> _introLines = [
    "Hi there. I'm AURA.",
    "Your intelligent assistant â€” built to help you think, create, and act.",
    "I remember your preferences, learn your habits, and get smarter over time.",
    "Let's set things up together. It only takes a minute.",
  ];

  int _visibleLines = 0;
  bool _introDone = false;

  final List<Map<String, String>> _conversation = [];
  final TextEditingController _textController = TextEditingController();

  String _selectedLanguage = "English";

  @override
  void initState() {
    super.initState();
    _playIntro();
  }

  void _playIntro() async {
    AccessibilityService().speak(_introLines.join(" "));
    for (int i = 0; i <= _introLines.length; i++) {
      await Future.delayed(const Duration(milliseconds: 2000));
      if (mounted) {
        setState(() {
          _visibleLines = i;
        });
      }
    }
    await Future.delayed(const Duration(milliseconds: 1000));
    if (mounted) {
      setState(() {
        _introDone = true;
      });
    }
  }

  void _nextStep() {
    if (_step == 0) {
      setState(() {
        _step = 1;
        _pushAura("What's your name? I'll use it every time we talk.");
      });
    } else if (_step == 1) {
      setState(() {
        _step = 2;
        _pushAura(
          "What do you do? Knowing your role helps me give better answers.",
        );
      });
    } else if (_step == 2) {
      setState(() {
        _step = 3;
        _pushAura(
          "Which language do you prefer we speak in? (English, Arabic, or Both)",
        );
      });
    } else if (_step == 3) {
      setState(() {
        _step = 4;
        _pushAura("Do you have any accessibility needs I should know about?");
      });
    } else if (_step == 4) {
      setState(() {
        _step = 5;
        _pushAura("What kinds of tasks do you most want AURA to help with?");
      });
    } else {
      // Finish
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (context) => const HomeWrapper()),
      );
    }
  }

  void _pushAura(String text) {
    _conversation.add({"role": "aura", "text": text});
    if (_step >= 3 && _selectedLanguage == "Arabic") {
      AccessibilityService().setLanguage("ar-EG");
    } else {
      AccessibilityService().setLanguage("en-US");
    }
    AccessibilityService().speak(text);
  }

  void _pushUser(String text) {
    setState(() {
      _conversation.add({"role": "user", "text": text});
    });
    if (_step == 3 && text.toLowerCase().contains("arabic")) {
      _selectedLanguage = "Arabic";
    }
    _nextStep();
  }

  void _submitText() {
    if (_textController.text.trim().isNotEmpty) {
      _pushUser(_textController.text.trim());
      _textController.clear();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AuraTheme.bgBase,
      body: SafeArea(
        child: _step == 0 ? _buildIntro() : _buildChatOnboarding(),
      ),
    );
  }

  Widget _buildIntro() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const CinematicIntro(size: 200),
          const SizedBox(height: 48),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24.0),
            child: Column(
              children: List.generate(
                _visibleLines < _introLines.length
                    ? _visibleLines
                    : _introLines.length,
                (index) => Padding(
                  padding: const EdgeInsets.only(bottom: 16.0),
                  child: AnimatedOpacity(
                    opacity: 1.0,
                    duration: const Duration(milliseconds: 500),
                    child: Text(
                      _introLines[index],
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.titleLarge?.copyWith(
                        color:
                            index == (_visibleLines - 1)
                                ? AuraTheme.textPrimary
                                : AuraTheme.textSecondary,
                        fontWeight:
                            index == (_visibleLines - 1)
                                ? FontWeight.bold
                                : FontWeight.normal,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
          const SizedBox(height: 32),
          if (_introDone)
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                backgroundColor: AuraTheme.pink400,
                foregroundColor: AuraTheme.bgBase,
                padding: const EdgeInsets.symmetric(
                  horizontal: 32,
                  vertical: 16,
                ),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(24),
                ),
              ),
              onPressed: _nextStep,
              child: const Text(
                "Let's get started â†’",
                style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildChatOnboarding() {
    return Column(
      children: [
        Expanded(
          child: ListView.builder(
            padding: const EdgeInsets.all(24.0),
            itemCount: _conversation.length,
            itemBuilder: (context, index) {
              final msg = _conversation[index];
              final isAura = msg["role"] == "aura";
              return Align(
                alignment:
                    isAura ? Alignment.centerLeft : Alignment.centerRight,
                child: Container(
                  margin: const EdgeInsets.only(bottom: 16.0),
                  padding: const EdgeInsets.all(16.0),
                  constraints: BoxConstraints(
                    maxWidth: MediaQuery.of(context).size.width * 0.75,
                  ),
                  decoration: BoxDecoration(
                    color:
                        isAura
                            ? AuraTheme.bgSurface
                            : AuraTheme.pink400.withOpacity(0.1),
                    borderRadius: BorderRadius.circular(16).copyWith(
                      bottomLeft:
                          isAura
                              ? const Radius.circular(0)
                              : const Radius.circular(16),
                      bottomRight:
                          !isAura
                              ? const Radius.circular(0)
                              : const Radius.circular(16),
                    ),
                    border: Border.all(
                      color:
                          isAura
                              ? AuraTheme.bgMuted
                              : AuraTheme.pink400.withOpacity(0.3),
                    ),
                  ),
                  child: Text(
                    msg["text"]!,
                    style: TextStyle(
                      color: isAura ? AuraTheme.textPrimary : AuraTheme.pink200,
                      fontSize: 16,
                    ),
                  ),
                ),
              );
            },
          ),
        ),
        Container(
          padding: const EdgeInsets.all(16.0),
          decoration: const BoxDecoration(
            color: AuraTheme.bgSurface,
            border: Border(top: BorderSide(color: AuraTheme.bgMuted)),
          ),
          child: Row(
            children: [
              IconButton(
                icon: const Icon(Icons.mic, color: AuraTheme.textSecondary),
                onPressed: () {
                  // In a real app we'd hook up Speech To Text here.
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text("Voice input active...")),
                  );
                },
              ),
              Expanded(
                child: TextField(
                  controller: _textController,
                  style: const TextStyle(color: AuraTheme.textPrimary),
                  decoration: InputDecoration(
                    hintText: "Type your answer...",
                    hintStyle: const TextStyle(color: AuraTheme.textMuted),
                    filled: true,
                    fillColor: AuraTheme.bgElevated,
                    contentPadding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 12,
                    ),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(24),
                      borderSide: BorderSide.none,
                    ),
                  ),
                  onSubmitted: (_) => _submitText(),
                ),
              ),
              const SizedBox(width: 8),
              IconButton(
                icon: const Icon(Icons.send, color: AuraTheme.pink400),
                onPressed: _submitText,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

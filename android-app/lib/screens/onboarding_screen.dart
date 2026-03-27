// lib/screens/onboarding_screen.dart
import 'dart:async';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;
import '../theme.dart';
import '../main.dart';

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

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key});

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  int _step = -1; // -1: language selection, 0+: questions
  String? _chosenLanguage;

  late stt.SpeechToText _speech;
  bool _isListening = false;

  final TextEditingController _textCtrl = TextEditingController();
  final FocusNode _focusNode = FocusNode();

  final List<String> _englishQuestions = [
    "What's your name? I'll use it every time we talk.",
    "What do you do? Knowing your role helps me give better answers.",
    "Any accessibility preferences I should know about?",
    "What kinds of tasks do you most want AURA to help with?",
  ];

  final List<String> _arabicQuestions = [
    "ما اسمك؟ سأستخدمه في كل مرة نتحدث فيها.",
    "ما هو عملك؟ معرفة دورك يساعدني في تقديم إجابات أفضل.",
    "هل هناك أي تفضيلات متعلقة بإمكانية الوصول يجب ألا أعرفها؟",
    "ما نوع المهام التي ترغب في أن تساعدك AURA فيها؟",
  ];

  List<String> get _currentQuestions =>
      _chosenLanguage == 'ar' ? _arabicQuestions : _englishQuestions;

  @override
  void initState() {
    super.initState();
    _speech = stt.SpeechToText();
  }

  @override
  void dispose() {
    _textCtrl.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _nextStep() {
    _textCtrl.clear();
    setState(() {
      _step++;
    });
    if (_step >= _currentQuestions.length) {
      Navigator.of(context).pushReplacement(
        PageRouteBuilder(
          pageBuilder: (_, __, ___) => const HomeWrapper(),
          transitionsBuilder:
              (_, a, __, child) => FadeTransition(opacity: a, child: child),
          transitionDuration: const Duration(milliseconds: 650),
        ),
      );
    }
  }

  void _toggleListening() async {
    if (_isListening) {
      _stopListening();
    } else {
      bool available = await _speech.initialize(
        onStatus: (status) {
          if (status == 'done' || status == 'notListening') {
            if (mounted) setState(() => _isListening = false);
          }
        },
      );
      if (available) {
        setState(() => _isListening = true);
        _speech.listen(
          onResult: (val) {
            String words = val.recognizedWords;
            String lower = words.toLowerCase();

            bool hasNextWord = false;
            String strippedWords = words;

            if (_chosenLanguage == 'en' && lower.contains('next')) {
              hasNextWord = true;
              strippedWords =
                  lower
                      .replaceAll(RegExp(r'\bnext\b', caseSensitive: false), '')
                      .trim();
            } else if (_chosenLanguage == 'ar' &&
                (lower.contains('تالي') ||
                    lower.contains('next') ||
                    lower.contains('التالي'))) {
              hasNextWord = true;
              strippedWords =
                  lower
                      .replaceAll('التالي', '')
                      .replaceAll('تالي', '')
                      .replaceAll(RegExp(r'\bnext\b', caseSensitive: false), '')
                      .trim();
            }

            if (mounted) {
              setState(() {
                _textCtrl.text = strippedWords;
              });
            }

            if (hasNextWord) {
              _stopListening();
              Future.delayed(const Duration(milliseconds: 400), () {
                if (mounted && _step < _currentQuestions.length) _nextStep();
              });
            }
          },
          localeId: _chosenLanguage == 'en' ? 'en_US' : 'ar_SA',
        );
      }
    }
  }

  void _stopListening() {
    _speech.stop();
    if (mounted) {
      setState(() => _isListening = false);
    }
  }

  void _onSubmit(String val) {
    String lower = val.toLowerCase();
    bool hasNextWord =
        (_chosenLanguage == 'en' && lower.contains('next')) ||
        (_chosenLanguage == 'ar' &&
            (lower.contains('تالي') ||
                lower.contains('next') ||
                lower.contains('التالي')));
    if (hasNextWord) {
      _textCtrl.text =
          lower
              .replaceAll('next', '')
              .replaceAll('التالي', '')
              .replaceAll('تالي', '')
              .trim();
      _nextStep();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF09080A),
      resizeToAvoidBottomInset: true,
      body: Stack(
        children: [
          Positioned.fill(
            child: Container(
              decoration: const BoxDecoration(
                gradient: RadialGradient(
                  center: Alignment(-0.8, -0.6),
                  radius: 1.5,
                  colors: [Color(0xFF2A101A), Color(0xFF09080A)],
                ),
              ),
            ),
          ),
          SafeArea(
            child: Column(
              children: [
                Expanded(
                  child: AnimatedSwitcher(
                    duration: const Duration(milliseconds: 650),
                    switchInCurve: Curves.easeOut,
                    switchOutCurve: Curves.easeIn,
                    transitionBuilder: (child, animation) {
                      return FadeTransition(opacity: animation, child: child);
                    },
                    child:
                        _step == -1
                            ? _buildLanguageSelection()
                            : _buildQuestionDeck(),
                  ),
                ),
                if (_step >= 0) _buildInputBar(),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildLanguageSelection() {
    return Center(
      key: const ValueKey('lang_sel'),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 24),
        child: _GlassCard(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const AnimatedQuestionText(
                text: "Select your language / اختر لغتك",
                isArabic: false,
              ),
              const SizedBox(height: 38),
              Row(
                children: [
                  Expanded(
                    child: _LangBtn(
                      label: "English",
                      onTap:
                          () => setState(() {
                            _chosenLanguage = 'en';
                            _step = 0;
                          }),
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: _LangBtn(
                      label: "العربية",
                      onTap:
                          () => setState(() {
                            _chosenLanguage = 'ar';
                            _step = 0;
                          }),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildQuestionDeck() {
    if (_step >= _currentQuestions.length) return const SizedBox.shrink();

    return Center(
      key: ValueKey('question_$_step'),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 24),
        child: _GlassCard(
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 120),
            child: Align(
              alignment:
                  _chosenLanguage == 'ar'
                      ? Alignment.centerRight
                      : Alignment.centerLeft,
              child: AnimatedQuestionText(
                text: _currentQuestions[_step],
                isArabic: _chosenLanguage == 'ar',
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildInputBar() {
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
      decoration: BoxDecoration(
        color: AuraTheme.bgElevated.withOpacity(0.5),
        border: Border(top: BorderSide(color: Colors.white.withOpacity(0.08))),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Expanded(
            child: Container(
              decoration: BoxDecoration(
                color: Colors.white.withOpacity(0.04),
                borderRadius: BorderRadius.circular(24),
                border: Border.all(color: Colors.white.withOpacity(0.1)),
              ),
              child: TextField(
                controller: _textCtrl,
                focusNode: _focusNode,
                style: _f(AuraTheme.textPrimary, size: 16),
                minLines: 1,
                maxLines: 3,
                textDirection:
                    _chosenLanguage == 'ar'
                        ? TextDirection.rtl
                        : TextDirection.ltr,
                decoration: InputDecoration(
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 20,
                    vertical: 14,
                  ),
                  hintText:
                      _chosenLanguage == 'ar'
                          ? 'تحدث للرد... أو قل "التالي"'
                          : 'Speak... or say "next"',
                  hintStyle: _f(AuraTheme.textDisabled, size: 14),
                  border: InputBorder.none,
                ),
                onSubmitted: _onSubmit,
              ),
            ),
          ),
          const SizedBox(width: 14),
          GestureDetector(
            onTap: _toggleListening,
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 300),
              width: 54,
              height: 54,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color:
                    _isListening
                        ? Colors.redAccent.withOpacity(0.9)
                        : AuraTheme.pink500,
                boxShadow:
                    _isListening
                        ? [
                          BoxShadow(
                            color: Colors.redAccent.withOpacity(0.4),
                            blurRadius: 16,
                            spreadRadius: 4,
                          ),
                        ]
                        : [
                          BoxShadow(
                            color: AuraTheme.pink500.withOpacity(0.2),
                            blurRadius: 8,
                            spreadRadius: 1,
                          ),
                        ],
              ),
              child: Icon(
                _isListening ? Icons.graphic_eq_rounded : Icons.mic_rounded,
                color: Colors.white,
                size: 26,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _GlassCard extends StatelessWidget {
  final Widget child;
  const _GlassCard({required this.child});

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(24),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 24, sigmaY: 24),
        child: Container(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(24),
            border: Border.all(color: Colors.white.withOpacity(0.12)),
            color: Colors.white.withOpacity(0.06),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withOpacity(0.2),
                blurRadius: 30,
                spreadRadius: -5,
              ),
            ],
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 38),
            child: child,
          ),
        ),
      ),
    );
  }
}

class _LangBtn extends StatelessWidget {
  final String label;
  final VoidCallback onTap;
  const _LangBtn({required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 18),
        decoration: BoxDecoration(
          color: Colors.white.withOpacity(0.1),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: Colors.white.withOpacity(0.2)),
        ),
        alignment: Alignment.center,
        child: Text(
          label,
          style: _f(
            Colors.white,
            weight: FontWeight.w600,
            size: 16,
            spacing: 0.5,
          ),
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
  late List<String> _words;
  int _visibleWords = 0;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _words = widget.text.split(' ');
    _startAnimation();
  }

  @override
  void didUpdateWidget(covariant AnimatedQuestionText oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.text != widget.text) {
      _timer?.cancel();
      _words = widget.text.split(' ');
      setState(() {
        _visibleWords = 0;
      });
      _startAnimation();
    }
  }

  void _startAnimation() {
    _timer = Timer.periodic(const Duration(milliseconds: 140), (timer) {
      if (_visibleWords < _words.length) {
        if (mounted) {
          setState(() {
            _visibleWords++;
          });
        }
      } else {
        timer.cancel();
      }
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    TextDirection textDirection =
        widget.isArabic ? TextDirection.rtl : TextDirection.ltr;

    return Directionality(
      textDirection: textDirection,
      child: Wrap(
        alignment: WrapAlignment.start,
        spacing: 6,
        runSpacing: 10,
        children: List.generate(_words.length, (i) {
          bool isVisible = i < _visibleWords;
          return AnimatedOpacity(
            opacity: isVisible ? 1.0 : 0.0,
            duration: const Duration(milliseconds: 400),
            child: AnimatedSlide(
              offset: isVisible ? Offset.zero : const Offset(0, 0.4),
              duration: const Duration(milliseconds: 400),
              curve: Curves.easeOutCubic,
              child: Text(
                _words[i],
                style: _f(
                  AuraTheme.textPrimary,
                  size: 26,
                  weight: FontWeight.w600,
                  height: 1.3,
                ),
              ),
            ),
          );
        }),
      ),
    );
  }
}

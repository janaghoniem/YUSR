// lib/screens/onboarding_screen.dart
import 'dart:async';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;
import 'package:video_player/video_player.dart';
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
  late VideoPlayerController _bgVideoController;

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
    _bgVideoController = VideoPlayerController.asset(
        'assets/aura_onboarding.webm',
      )
      ..initialize().then((_) {
        _bgVideoController.setLooping(true);
        _bgVideoController.play();
        if (mounted) setState(() {});
      });
  }

  @override
  void dispose() {
    _bgVideoController.dispose();
    _textCtrl.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _announceToScreenReader(String message) {
    SemanticsService.announce(
      message,
      _chosenLanguage == 'ar' ? TextDirection.rtl : TextDirection.ltr,
    );
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
    } else if (_step >= 0) {
      _announceToScreenReader(_currentQuestions[_step]);
      // Wait for transition and then auto-start listening
      Future.delayed(const Duration(milliseconds: 600), _startListening);
    }
  }

  void _setLanguage(String lang) {
    setState(() {
      _chosenLanguage = lang;
      _step = 0;
    });
    _announceToScreenReader(_currentQuestions[0]);
    Future.delayed(const Duration(milliseconds: 600), _startListening);
  }

  Future<void> _startListening() async {
    if (_isListening) return;

    bool available = await _speech.initialize(
      onStatus: (status) {
        if (status == 'done' || status == 'notListening') {
          if (mounted) {
            setState(() => _isListening = false);
            _announceToScreenReader(
              _chosenLanguage == 'ar'
                  ? 'تم إيقاف الميكروفون'
                  : 'Microphone paused',
            );
          }
        }
      },
      onError: (val) {
        if (mounted) {
          setState(() => _isListening = false);
        }
      },
    );

    if (available) {
      setState(() => _isListening = true);
      _announceToScreenReader(
        _chosenLanguage == 'ar'
            ? 'الميكروفون يعمل، يرجى التحدث'
            : 'Microphone is on, please speak',
      );

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

  void _stopListening() {
    _speech.stop();
    if (mounted) {
      setState(() => _isListening = false);
    }
  }

  void _toggleListening() {
    if (_isListening) {
      _stopListening();
    } else {
      _startListening();
    }
  }

  // Called via send button or done keyboard action
  void _onSubmit(String val) {
    if (val.trim().isNotEmpty) {
      _stopListening(); // Make sure mic resets cleanly when manually submitting
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
            child:
                _bgVideoController.value.isInitialized
                    ? FittedBox(
                      fit: BoxFit.cover,
                      child: SizedBox(
                        width: _bgVideoController.value.size.width,
                        height: _bgVideoController.value.size.height,
                        child: VideoPlayer(_bgVideoController),
                      ),
                    )
                    : Container(
                      decoration: const BoxDecoration(
                        gradient: RadialGradient(
                          center: Alignment(-0.8, -0.6),
                          radius: 1.5,
                          colors: [Color(0xFF2A101A), Color(0xFF09080A)],
                        ),
                      ),
                    ),
          ),
          // Positioned.fill(
          //   child: Container(
          //     color: Colors.black.withOpacity(
          //       0.1,
          //     ), // Dark overlay for text readability
          //   ),
          // ),
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
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Semantics(
              header: true,
              child: AnimatedQuestionText(
                text: "Select your language\nاختر لغتك",
                isArabic: false,
              ),
            ),
            const SizedBox(height: 48),
            Row(
              children: [
                Expanded(
                  child: Semantics(
                    button: true,
                    label: "Choose English",
                    child: _LangBtn(
                      label: "English",
                      onTap: () => _setLanguage('en'),
                    ),
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Semantics(
                    button: true,
                    label: "اختر اللغة العربية",
                    child: _LangBtn(
                      label: "العربية",
                      onTap: () => _setLanguage('ar'),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildQuestionDeck() {
    if (_step >= _currentQuestions.length) return const SizedBox.shrink();

    return Center(
      key: ValueKey('question_$_step'),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 32),
        // Removed GlassCard container entirely as per user request to put it straight on screen
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 120),
          child: Semantics(
            liveRegion: true,
            header: true,
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
    return Semantics(
      label: _chosenLanguage == 'ar' ? 'شريط الإدخال' : 'Input bar',
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
        decoration: BoxDecoration(
          color: AuraTheme.bgElevated.withOpacity(0.5),
          border: Border(
            top: BorderSide(color: Colors.white.withOpacity(0.08)),
          ),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            // TextField Area
            Expanded(
              child: Semantics(
                textField: true,
                label:
                    _chosenLanguage == 'ar'
                        ? 'حقل النص للإجابة'
                        : 'Response text field',
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
                              ? 'تحدث للرد... أو الصق إجابتك'
                              : 'Speak your response...',
                      hintStyle: _f(AuraTheme.textDisabled, size: 14),
                      border: InputBorder.none,
                    ),
                    onSubmitted: _onSubmit,
                  ),
                ),
              ),
            ),
            const SizedBox(width: 10),

            // Send Button
            Semantics(
              button: true,
              label:
                  _chosenLanguage == 'ar' ? 'إرسال الإجابة' : 'Send response',
              child: Tooltip(
                message: _chosenLanguage == 'ar' ? 'إرسال' : 'Send',
                child: InkWell(
                  onTap: () => _onSubmit(_textCtrl.text),
                  borderRadius: BorderRadius.circular(30),
                  child: Container(
                    width: 48,
                    height: 48,
                    decoration: BoxDecoration(
                      color: Colors.white.withOpacity(0.08),
                      shape: BoxShape.circle,
                      border: Border.all(color: Colors.white.withOpacity(0.1)),
                    ),
                    child: const Icon(
                      Icons.send_rounded,
                      color: Colors.white,
                      size: 22,
                    ),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 8),

            // Sleek Glassmorphism Mic Button
            Semantics(
              button: true,
              label:
                  _chosenLanguage == 'ar'
                      ? 'تشغيل أو إيقاف الميكروفون'
                      : 'Toggle Microphone',
              toggled: _isListening,
              child: Tooltip(
                message: _chosenLanguage == 'ar' ? 'ميكروفون' : 'Microphone',
                child: GestureDetector(
                  onTap: _toggleListening,
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 300),
                    width: 56,
                    height: 56,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      border: Border.all(
                        color:
                            _isListening
                                ? Colors.redAccent.withOpacity(0.8)
                                : Colors.white.withOpacity(0.15),
                        width: 1.5,
                      ),
                      boxShadow:
                          _isListening
                              ? [
                                BoxShadow(
                                  color: Colors.redAccent.withOpacity(0.6),
                                  blurRadius: 20,
                                  spreadRadius: 4,
                                ),
                                BoxShadow(
                                  color: Colors.white.withOpacity(0.2),
                                  blurRadius: 10,
                                  spreadRadius: -2,
                                ),
                              ]
                              : [
                                BoxShadow(
                                  color: Colors.black.withOpacity(0.2),
                                  blurRadius: 12,
                                  spreadRadius: 2,
                                ),
                              ],
                      gradient:
                          _isListening
                              ? RadialGradient(
                                colors: [
                                  Colors.redAccent.shade200,
                                  Colors.red.shade800,
                                ],
                              )
                              : LinearGradient(
                                begin: Alignment.topLeft,
                                end: Alignment.bottomRight,
                                colors: [
                                  Colors.white.withOpacity(0.15),
                                  Colors.white.withOpacity(0.05),
                                ],
                              ),
                    ),
                    child: ClipOval(
                      child: BackdropFilter(
                        filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
                        child: Center(
                          child: Icon(
                            _isListening
                                ? Icons.hearing_rounded
                                : Icons.mic_rounded,
                            color: Colors.white,
                            size: 28,
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
        height: 64, // Large min touch target for accessibility
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
            size: 18,
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
    _words =
        widget.text
            .split(RegExp(r'(?<=\n)|(?=\n)|\s+'))
            .where((s) => s.isNotEmpty)
            .toList();
    _startAnimation();
  }

  @override
  void didUpdateWidget(covariant AnimatedQuestionText oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.text != widget.text) {
      _timer?.cancel();
      _words =
          widget.text
              .split(RegExp(r'(?<=\n)|(?=\n)|\s+'))
              .where((s) => s.isNotEmpty)
              .toList();
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
        spacing: 8,
        runSpacing: 10,
        children: List.generate(_words.length, (i) {
          if (_words[i] == '\n') {
            return const SizedBox(width: double.infinity, height: 0);
          }
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
                  size:
                      32, // Slightly larger since it's naked on the screen now
                  weight: FontWeight.w600,
                  height: 1.4,
                ),
              ),
            ),
          );
        }),
      ),
    );
  }
}

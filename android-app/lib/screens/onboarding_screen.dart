// lib/screens/onboarding_screen.dart
// REQ 5: Accessibility permission removed from main text input screen.
// REQ 8: All screens read aloud via TTS on appearance.
import 'dart:async';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter/services.dart';
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
  int _step = -1;
  String? _chosenLanguage;

  late stt.SpeechToText _speech;
  bool _isListening = false;
  bool _isHandlingNext = false;
  late VideoPlayerController _bgVideoController;

  // REQ 8: TTS channel
  static const _tts = MethodChannel('com.example.automation/tts');

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
    // REQ 3: aura_calm.webm for onboarding questions
    _bgVideoController = VideoPlayerController.asset('assets/aura_calm.webm')
      ..initialize().then((_) {
        _bgVideoController.setLooping(true);
        _bgVideoController.play();
        if (mounted) setState(() {});
      });

    // REQ 8: speak on appearance
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _speakTTS('Select your language. اختر لغتك.');
    });
  }

  Future<void> _speakTTS(String text) async {
    try {
      await _tts.invokeMethod('speak', {'text': text});
    } catch (_) {}
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
    setState(() { _step++; });

    if (_step > _currentQuestions.length) {
      Navigator.of(context).pushReplacement(
        PageRouteBuilder(
          pageBuilder: (_, __, ___) => const HomeWrapper(),
          transitionsBuilder: (_, a, __, child) => FadeTransition(opacity: a, child: child),
          transitionDuration: const Duration(milliseconds: 650),
        ),
      );
    } else if (_step == _currentQuestions.length) {
      // REQ 5: NO accessibility prompt here — just navigate home
      Navigator.of(context).pushReplacement(
        PageRouteBuilder(
          pageBuilder: (_, __, ___) => const HomeWrapper(),
          transitionsBuilder: (_, a, __, child) => FadeTransition(opacity: a, child: child),
          transitionDuration: const Duration(milliseconds: 650),
        ),
      );
    } else if (_step >= 0) {
      final q = _currentQuestions[_step];
      _announceToScreenReader(q);
      // REQ 8: read each question aloud
      _speakTTS(q);
      Future.delayed(const Duration(milliseconds: 600), _startListening);
    }
  }

  void _setLanguage(String lang) {
    setState(() {
      _chosenLanguage = lang;
      _step = 0;
    });
    final q = _currentQuestions[0];
    _announceToScreenReader(q);
    _speakTTS(q);
    Future.delayed(const Duration(milliseconds: 600), _startListening);
  }

  Future<void> _startListening() async {
    if (_isListening) return;

    bool available = await _speech.initialize(
      onStatus: (status) {
        if (status == 'done' || status == 'notListening') {
          if (mounted) setState(() => _isListening = false);
        }
      },
      onError: (val) {
        if (mounted) setState(() => _isListening = false);
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
            strippedWords = lower.replaceAll(RegExp(r'\bnext\b', caseSensitive: false), '').trim();
          } else if (_chosenLanguage == 'ar' &&
              (lower.contains('تالي') || lower.contains('next') || lower.contains('التالي'))) {
            hasNextWord = true;
            strippedWords = lower.replaceAll('التالي', '').replaceAll('تالي', '').replaceAll(RegExp(r'\bnext\b', caseSensitive: false), '').trim();
          }

          if (mounted) setState(() => _textCtrl.text = strippedWords);

          if (hasNextWord && !_isHandlingNext) {
            _isHandlingNext = true;
            _stopListening();
            Future.delayed(const Duration(milliseconds: 500), () {
              if (mounted && _step < _currentQuestions.length) {
                _nextStep();
                _isHandlingNext = false;
              }
            });
          }
        },
        localeId: _chosenLanguage == 'en' ? 'en_US' : 'ar_SA',
      );
    }
  }

  void _stopListening() {
    _speech.stop();
    if (mounted) setState(() => _isListening = false);
  }

  void _toggleListening() {
    if (_isListening) _stopListening();
    else _startListening();
  }

  void _onSubmit(String val) {
    if (val.trim().isNotEmpty) {
      _stopListening();
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
            child: _bgVideoController.value.isInitialized
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
          SafeArea(
            child: Column(
              children: [
                Expanded(
                  child: AnimatedSwitcher(
                    duration: const Duration(milliseconds: 650),
                    switchInCurve: Curves.easeOut,
                    switchOutCurve: Curves.easeIn,
                    transitionBuilder: (child, animation) =>
                        FadeTransition(opacity: animation, child: child),
                    child: _step == -1
                        ? _buildLanguageSelection()
                        : _buildQuestionDeck(),
                  ),
                ),
                if (_step >= 0 && _step < _currentQuestions.length)
                  _buildInputBar(),
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
            AnimatedQuestionText(
              text: "Select your language\nاختر لغتك",
              isArabic: false,
            ),
            const SizedBox(height: 48),
            Row(
              children: [
                Expanded(child: _LangBtn(label: "English", onTap: () => _setLanguage('en'))),
                const SizedBox(width: 16),
                Expanded(child: _LangBtn(label: "العربية", onTap: () => _setLanguage('ar'))),
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
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 120),
          child: Align(
            alignment: _chosenLanguage == 'ar' ? Alignment.centerRight : Alignment.centerLeft,
            child: AnimatedQuestionText(
              text: _currentQuestions[_step],
              isArabic: _chosenLanguage == 'ar',
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildInputBar() {
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 24),
      decoration: BoxDecoration(
        color: Colors.black.withOpacity(0.35),
        border: Border(top: BorderSide(color: Colors.white.withOpacity(0.07))),
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(0),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 20, sigmaY: 20),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              // Text field — REQ 15: glassmorphic
              Expanded(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(24),
                  child: BackdropFilter(
                    filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
                    child: Container(
                      decoration: BoxDecoration(
                        color: Colors.white.withOpacity(0.05),
                        borderRadius: BorderRadius.circular(24),
                        border: Border.all(color: Colors.white.withOpacity(0.1)),
                      ),
                      child: TextField(
                        controller: _textCtrl,
                        focusNode: _focusNode,
                        style: _f(AuraTheme.textPrimary, size: 15),
                        minLines: 1,
                        maxLines: 3,
                        textDirection: _chosenLanguage == 'ar' ? TextDirection.rtl : TextDirection.ltr,
                        decoration: InputDecoration(
                          contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 13),
                          hintText: _chosenLanguage == 'ar' ? 'تحدث للرد...' : 'Speak your response...',
                          hintStyle: _f(AuraTheme.textDisabled, size: 13),
                          border: InputBorder.none,
                        ),
                        onSubmitted: _onSubmit,
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 10),

              // Send button — REQ 11
              GestureDetector(
                onTap: () => _onSubmit(_textCtrl.text),
                child: Container(
                  width: 48,
                  height: 48,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: const LinearGradient(
                      colors: [AuraTheme.pink500, AuraTheme.pink700],
                    ),
                    boxShadow: [
                      BoxShadow(
                        color: AuraTheme.pink500.withOpacity(0.35),
                        blurRadius: 12,
                        offset: const Offset(0, 3),
                      ),
                    ],
                  ),
                  child: const Icon(Icons.send_rounded, color: Colors.white, size: 20),
                ),
              ),
              const SizedBox(width: 8),

              // Mic button — REQ 11, glassmorphic
              GestureDetector(
                onTap: _toggleListening,
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 250),
                  width: 52,
                  height: 52,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: _isListening
                        ? const RadialGradient(colors: [Color(0xFFFF5252), Color(0xFFB71C1C)])
                        : LinearGradient(
                            begin: Alignment.topLeft,
                            end: Alignment.bottomRight,
                            colors: [
                              Colors.white.withOpacity(0.13),
                              Colors.white.withOpacity(0.04),
                            ],
                          ),
                    border: Border.all(
                      color: _isListening
                          ? Colors.redAccent.withOpacity(0.75)
                          : Colors.white.withOpacity(0.15),
                      width: 1.3,
                    ),
                    boxShadow: _isListening
                        ? [BoxShadow(color: Colors.redAccent.withOpacity(0.5), blurRadius: 18, spreadRadius: 3)]
                        : [],
                  ),
                  child: ClipOval(
                    child: BackdropFilter(
                      filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
                      child: Center(
                        child: Icon(
                          _isListening ? Icons.hearing_rounded : Icons.mic_rounded,
                          color: Colors.white,
                          size: 26,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ],
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
      child: ClipRRect(
        borderRadius: BorderRadius.circular(16),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
          child: Container(
            height: 62,
            decoration: BoxDecoration(
              color: Colors.white.withOpacity(0.08),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: Colors.white.withOpacity(0.2)),
            ),
            alignment: Alignment.center,
            child: Text(
              label,
              style: _f(Colors.white, weight: FontWeight.w600, size: 17, spacing: 0.3),
            ),
          ),
        ),
      ),
    );
  }
}

class AnimatedQuestionText extends StatefulWidget {
  final String text;
  final bool isArabic;
  const AnimatedQuestionText({super.key, required this.text, this.isArabic = false});

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
    _words = widget.text.split(RegExp(r'(?<=\n)|(?=\n)|\s+')).where((s) => s.isNotEmpty).toList();
    _startAnimation();
  }

  @override
  void didUpdateWidget(covariant AnimatedQuestionText oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.text != widget.text) {
      _timer?.cancel();
      _words = widget.text.split(RegExp(r'(?<=\n)|(?=\n)|\s+')).where((s) => s.isNotEmpty).toList();
      setState(() => _visibleWords = 0);
      _startAnimation();
    }
  }

  void _startAnimation() {
    _timer = Timer.periodic(const Duration(milliseconds: 130), (timer) {
      if (_visibleWords < _words.length) {
        if (mounted) setState(() => _visibleWords++);
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
    return Directionality(
      textDirection: widget.isArabic ? TextDirection.rtl : TextDirection.ltr,
      child: Wrap(
        alignment: WrapAlignment.start,
        spacing: 7,
        runSpacing: 9,
        children: List.generate(_words.length, (i) {
          if (_words[i] == '\n') return const SizedBox(width: double.infinity, height: 0);
          final isVisible = i < _visibleWords;
          return AnimatedOpacity(
            opacity: isVisible ? 1.0 : 0.0,
            duration: const Duration(milliseconds: 380),
            child: AnimatedSlide(
              offset: isVisible ? Offset.zero : const Offset(0, 0.35),
              duration: const Duration(milliseconds: 380),
              curve: Curves.easeOutCubic,
              child: Text(
                _words[i],
                style: _f(AuraTheme.textPrimary, size: 30, weight: FontWeight.w600, height: 1.35),
              ),
            ),
          );
        }),
      ),
    );
  }
}
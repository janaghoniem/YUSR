import 'package:flutter_tts/flutter_tts.dart';
import 'package:speech_to_text/speech_to_text.dart';

class AccessibilityService {
  static final AccessibilityService _instance =
      AccessibilityService._internal();
  factory AccessibilityService() => _instance;

  final FlutterTts _flutterTts = FlutterTts();
  final SpeechToText _speechToText = SpeechToText();
  bool _isListeningContinuous = false;
  String _lastRecognizedText = '';
  Function(String)? onSpeechRecognized;

  AccessibilityService._internal() {
    _initTts();
    _initStt();
  }

  Future<void> _initTts() async {
    await _flutterTts.setSharedInstance(true);
    await _flutterTts
        .setIosAudioCategory(IosTextToSpeechAudioCategory.playback, [
          IosTextToSpeechAudioCategoryOptions.allowBluetooth,
          IosTextToSpeechAudioCategoryOptions.allowBluetoothA2DP,
          IosTextToSpeechAudioCategoryOptions.mixWithOthers,
        ]);

    // Default config
    await _flutterTts.setVolume(1.0);
    await _flutterTts.setSpeechRate(0.5);
    await _flutterTts.setPitch(1.0);
  }

  Future<void> _initStt() async {
    await _speechToText.initialize(
      onError: (val) async {
        if (_isListeningContinuous) {
          // Restart listening on error if continuous
          await Future.delayed(const Duration(milliseconds: 50));
          startContinuousListening(onSpeechRecognized);
        }
      },
      onStatus: (val) async {
        if (val == 'done' || val == 'notListening') {
          if (_isListeningContinuous) {
            await Future.delayed(const Duration(milliseconds: 50));
            startContinuousListening(onSpeechRecognized);
          }
        }
      },
    );
  }

  // Set language: Supports ar-EG for Egyptian Arabic, en-US for English
  Future<void> setLanguage(String languageCode) async {
    await _flutterTts.setLanguage(languageCode);
  }

  Future<void> speak(String text) async {
    await _flutterTts.speak(text);
  }

  Future<void> stop() async {
    await _flutterTts.stop();
  }

  Future<void> startContinuousListening(Function(String)? onRecognized) async {
    onSpeechRecognized = onRecognized;
    _isListeningContinuous = true;

    // Check initialization before listening
    bool available = await _speechToText.initialize();
    if (available && !_speechToText.isListening) {
      await _speechToText.listen(
        onResult: (val) {
          if (val.recognizedWords.isNotEmpty && val.finalResult) {
            _lastRecognizedText = val.recognizedWords;
            if (onSpeechRecognized != null) {
              // Parse basic navigation commands
              String lowerText = _lastRecognizedText.toLowerCase();
              if (lowerText.contains("go back") ||
                  lowerText.contains("home") ||
                  lowerText.contains("scroll")) {
                print("Navigation Command Detected: $_lastRecognizedText");
                // Here Android Intents or MethodChannels can be triggered for global nav
              }
              onSpeechRecognized!(_lastRecognizedText);
            }
          }
        },
        listenMode: ListenMode.dictation,
        cancelOnError: false,
        partialResults: false,
      );
    }
  }

  Future<void> stopContinuousListening() async {
    _isListeningContinuous = false;
    onSpeechRecognized = null;
    if (_speechToText.isListening) {
      await _speechToText.stop();
    }
  }
}

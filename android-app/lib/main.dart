import 'dart:convert';
import 'dart:async';
import 'dart:io';
import 'dart:math';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:http/http.dart' as http;
import 'package:permission_handler/permission_handler.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:speech_to_text/speech_recognition_error.dart' as stt;
import 'package:speech_to_text/speech_recognition_result.dart' as stt;
import 'package:speech_to_text/speech_to_text.dart' as stt;
import 'package:video_player/video_player.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'screens/startup_screen.dart';
import 'theme.dart';

TextStyle _f(
  Color color, {
  FontWeight weight = FontWeight.w400,
  double size = 14,
  double spacing = 0,
  double height = 1.4,
}) => TextStyle(
  fontFamily: 'PlusJakartaSans',
  color: color,
  fontWeight: weight,
  fontSize: size,
  letterSpacing: spacing,
  height: height,
);

void main() {
  runApp(const MyApp());
}

/// Helper function to check if a language is RTL (Right-to-Left)
bool isRTLLanguage(String languageCode) {
  const rtlLanguages = {'ar', 'he', 'fa', 'ur', 'yi', 'iw'};
  return rtlLanguages.contains(languageCode.toLowerCase());
}

/// Get text direction for a language
TextDirection getTextDirection(String languageCode) {
  return isRTLLanguage(languageCode) ? TextDirection.rtl : TextDirection.ltr;
}

class MyApp extends StatefulWidget {
  const MyApp({super.key});

  @override
  State<MyApp> createState() => _MyAppState();
}

class _MyAppState extends State<MyApp> {
  String _languageCode = 'en';
  late Future<void> _initLanguage;

  @override
  void initState() {
    super.initState();
    _initLanguage = _loadLanguagePreference();
  }

  Future<void> _loadLanguagePreference() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final stored = prefs.getString('preferredLanguage') ?? 'en';
      setState(() {
        _languageCode = stored;
      });
    } catch (e) {
      print('[RTL] Failed to load language preference: $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'AURA',
      theme: AuraTheme.darkTheme,
      // ✅ Set locale and text direction based on language preference
      locale: Locale(_languageCode),
      builder: (context, child) {
        return Directionality(
          textDirection: getTextDirection(_languageCode),
          child: child ?? const SizedBox.shrink(),
        );
      },
      home: const StartupScreen(),
    );
  }
}

// ─── Device Manager ───────────────────────────────────────────────────────────
class DeviceManager {
  static const String backendUrl = 'http://10.0.2.2:8000';
  static const String deviceId = 'flutter_device';
  static const MethodChannel _platform = MethodChannel(
    'com.example.automation/service',
  );

  static Future<bool> registerDevice({
    required String userId,
    required String sessionId,
    String platform = 'mobile',
  }) async {
    try {
      final response = await http.post(
        Uri.parse('$backendUrl/device/$deviceId/register'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'user_id': userId,
          'session_id': sessionId,
          'platform': platform,
          'name': 'Flutter Device',
          'android_version': '14',
          'device_model': 'Emulator',
          'screen_width': 1080,
          'screen_height': 2340,
        }),
      );
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  static Future<bool> sendUITree(Map<String, dynamic> tree) async {
    try {
      final response = await http.post(
        Uri.parse('$backendUrl/device/$deviceId/ui-tree'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode(tree),
      );
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  static Future<bool> sendStatus() async {
    try {
      final response = await http.post(
        Uri.parse('$backendUrl/device/$deviceId/status'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'status': 'online',
          'android_version': '14',
          'screen_width': 1080,
          'screen_height': 2340,
        }),
      );
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  static Future<Map<String, dynamic>> getAccessibilityTree() async {
    try {
      final result = await _platform.invokeMethod('getAccessibilityTree');
      if (result is String) {
        return jsonDecode(result) as Map<String, dynamic>;
      }
      if (result is Map) {
        return Map<String, dynamic>.from(result);
      }
      return {};
    } catch (_) {
      return {};
    }
  }

  static Future<Map<String, dynamic>> executeAction(
    Map<String, dynamic> action,
  ) async {
    try {
      final type = action['action_type']?.toString() ?? '';
      switch (type) {
        case 'click':
          await _platform.invokeMethod('executeAction', {
            'action_type': 'click',
            'element_id': action['element_id'],
          });
          await Future.delayed(const Duration(milliseconds: 1500));
          final tree = await getAccessibilityTree();
          if (tree.isNotEmpty) {
            await sendUITree(tree);
          }
          break;
        case 'type':
          await _platform.invokeMethod('executeAction', {
            'action_type': 'type',
            'element_id': action['element_id'],
            'text': action['text'],
          });
          await Future.delayed(const Duration(milliseconds: 500));
          final tree = await getAccessibilityTree();
          if (tree.isNotEmpty) {
            await sendUITree(tree);
          }
          break;
        case 'scroll':
          await _platform.invokeMethod('executeAction', {
            'action_type': 'scroll',
            'direction': action['direction'] ?? 'down',
          });
          await Future.delayed(const Duration(milliseconds: 800));
          final tree = await getAccessibilityTree();
          if (tree.isNotEmpty) {
            await sendUITree(tree);
          }
          break;
        case 'wait':
          await Future.delayed(
            Duration(milliseconds: (action['duration'] ?? 500) as int),
          );
          break;
        case 'navigate_home':
        case 'goToHome':
          await _platform.invokeMethod('executeAction', {
            'action_type': 'global_action',
            'action_name': 'HOME',
          });
          await Future.delayed(const Duration(milliseconds: 2500));
          final tree = await getAccessibilityTree();
          if (tree.isNotEmpty) {
            await sendUITree(tree);
          }
          break;
        case 'navigate_back':
          await _platform.invokeMethod('executeAction', {
            'action_type': 'global_action',
            'action_name': 'BACK',
          });
          await Future.delayed(const Duration(milliseconds: 1000));
          final tree = await getAccessibilityTree();
          if (tree.isNotEmpty) {
            await sendUITree(tree);
          }
          break;
        case 'global_action':
          final name =
              action['global_action'] ?? action['action_name'] ?? 'HOME';
          await _platform.invokeMethod('executeAction', {
            'action_type': 'global_action',
            'action_name': name,
          });
          await Future.delayed(const Duration(milliseconds: 1000));
          final tree = await getAccessibilityTree();
          if (tree.isNotEmpty) {
            await sendUITree(tree);
          }
          break;
      }

      return {
        'action_id': action['action_id'] ?? 'unknown',
        'success': true,
        'execution_time_ms': 100,
      };
    } catch (e) {
      return {
        'action_id': action['action_id'] ?? 'unknown',
        'success': false,
        'error': e.toString(),
        'execution_time_ms': 0,
      };
    }
  }
}

class AppColors {
  static const darkPlum4 = Color(0xFF1B0E25);
}

class HomeWrapper extends StatelessWidget {
  const HomeWrapper({super.key});
  @override
  Widget build(BuildContext context) => const AutomationDemo();
}

class AutomationDemo extends StatefulWidget {
  final String userId;
  final String username;
  final String sessionId;
  final String language;

  const AutomationDemo({
    super.key,
    this.userId = 'flutter_user',
    this.username = 'User',
    this.sessionId = '',
    this.language = 'en',
  });

  @override
  State<AutomationDemo> createState() => _AutomationDemoState();
}

class _AutomationDemoState extends State<AutomationDemo>
    with TickerProviderStateMixin {
  static const _platform = MethodChannel('com.example.automation/service');

  late String _activeUserId;
  late String _activeSessionId;
  String _status = 'Waiting...';
  bool _serviceEnabled = false;
  bool _isLoading = false;
  bool _isExecuting = false; // REQ 14: for widget morph
  bool _isPaused = false; // REQ 16: pause state
  bool _isRecording = false;
  bool _isSidebarOpen = false;
  bool _chatMode = false;
  bool _showSettings = false;
  bool _showExecutionWidget = false;
  bool _executionWidgetMinimized = true;
  bool _executionNeedsAttention = false;
  int _executionDismissToken = 0;
  String _executionWidgetTitle = 'Executing task';
  String _executionWidgetSubtitle = 'AURA is running in background';
  int _activeSettingsSection = 0;

  List<Map<String, dynamic>> _chats = [];
  bool _chatsLoading = false;

  String? _viewingSessionId;
  String? _viewingTitle;
  List<Map<String, dynamic>> _viewingMessages = [];
  bool _viewingLoading = false;

  late TextEditingController _usernameSettingsCtrl;
  late TextEditingController _emailSettingsCtrl;
  bool _profileSaving = false;
  String? _profileSaveStatus;
  final TextEditingController _textCtrl = TextEditingController();
  String _responseText = '';
  String _transcribedText = '';
  String _confirmationRequest = '';
  String _draftedMessage = '';
  bool _needsConfirmation = false;
  bool _isThinking = false;
  bool _speechAvailable = false;
  bool _alwaysListening = true;
  bool _awaitingCommandAfterWake = false;
  bool _manualListeningLatch = false;
  bool _speechInitialized = false;
  bool _restartScheduled = false;
  bool _speechRecoveryInProgress = false;
  DateTime _lastListenStart = DateTime.fromMillisecondsSinceEpoch(0);
  DateTime _lastListenStop = DateTime.fromMillisecondsSinceEpoch(0);
  final List<String> _thinkingSteps = [];
  late String _userName;

  // REQ 4: accessibility toggle in settings
  bool _accessibilityEnabled = false;

  HttpServer? _actionServer;
  late AnimationController _pulseCtrl;
  late AnimationController _thinkCtrl;
  late AnimationController _waveCtrl;
  late VideoPlayerController _videoCtrl;
  late stt.SpeechToText _speechToText;
  late FlutterTts _flutterTts;
  WebSocketChannel? _wsChannel;
  StreamSubscription? _wsSub;

  @override
  void initState() {
    super.initState();
    _activeUserId = widget.userId;
    _activeSessionId =
        widget.sessionId.isEmpty
            ? 'flutter_${DateTime.now().millisecondsSinceEpoch}'
            : widget.sessionId;
    _userName = widget.username.isNotEmpty ? widget.username : 'User';
    _usernameSettingsCtrl = TextEditingController(text: _userName);
    _emailSettingsCtrl = TextEditingController();
    _setupMethodChannelListener();
    _loadChats();
    _loadProfile();
    _checkServiceStatus();
    _registerWithBackend();
    _startActionServer();
    _startPollingForActions();
    _loadAccessibilityPref();
    _speechToText = stt.SpeechToText();
    _flutterTts = FlutterTts();
    _initSpeechAndTts();
    _connectThinkingSocket();

    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    );
    _thinkCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1000),
    );
    _waveCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);

    _videoCtrl = VideoPlayerController.asset('assets/aura_main.mp4')
      ..initialize().then((_) {
        setState(() {});
        _videoCtrl
          ..setLooping(true)
          ..play();
      });
  }

  Future<void> _initSpeechAndTts() async {
    final mic = await Permission.microphone.request();
    final canUseMic = mic.isGranted || mic.isLimited;
    bool canUseSpeech = true;
    if (!Platform.isAndroid) {
      final speech = await Permission.speech.request();
      canUseSpeech = speech.isGranted || speech.isLimited;
    }

    if (!canUseMic || !canUseSpeech) {
      setState(() {
        _speechAvailable = false;
        _status = 'Microphone permission is required for voice control.';
      });
      return;
    }

    final available = await _speechToText.initialize(
      onStatus: _onSpeechStatus,
      onError: _onSpeechError,
    );

    await _flutterTts.setLanguage(widget.language == 'ar' ? 'ar-EG' : 'en-US');
    await _flutterTts.setPitch(1.0);
    await _flutterTts.setSpeechRate(0.46);

    if (!mounted) return;
    setState(() {
      _speechAvailable = available;
      _speechInitialized = available;
      _status =
          available ? _status : 'Speech recognition unavailable on device.';
    });

    if (available && !_chatMode) {
      _beginListening();
    }
  }

  void _onSpeechStatus(String status) {
    if (!mounted) return;
    final lower = status.toLowerCase();
    if (lower.contains('listening')) {
      setState(() {
        _isRecording = true;
      });
      if (!_pulseCtrl.isAnimating) {
        _pulseCtrl.repeat(reverse: true);
      }
      return;
    }

    if (lower.contains('notlistening') || lower.contains('done')) {
      setState(() {
        _isRecording = !_chatMode && _alwaysListening;
      });
      if (_chatMode) {
        _pulseCtrl.stop();
        _pulseCtrl.reset();
      }
      _lastListenStop = DateTime.now();
      _scheduleSpeechRestart();
    }
  }

  void _scheduleSpeechRestart() {
    if (!_alwaysListening ||
        !_speechInitialized ||
        _restartScheduled ||
        _chatMode) {
      return;
    }
    _restartScheduled = true;
    Future.delayed(const Duration(milliseconds: 650), () async {
      _restartScheduled = false;
      if (!mounted || !_alwaysListening || _chatMode) return;
      await _beginListening();
    });
  }

  void _onSpeechError(stt.SpeechRecognitionError err) {
    if (!mounted) return;

    final msg = err.errorMsg.toLowerCase();
    final permissionIssue =
        msg.contains('permission') ||
        msg.contains('not allowed') ||
        msg.contains('denied');

    if (permissionIssue) {
      setState(() {
        _isRecording = false;
        _speechAvailable = false;
        _speechInitialized = false;
        _status = 'Microphone permission is required for voice control.';
      });
      return;
    }

    setState(() {
      _isRecording = false;
      if (_status.toLowerCase().contains('retrying')) {
        _status = 'Reconnecting voice recognition...';
      }
    });

    if (err.permanent) {
      _speechInitialized = false;
      unawaited(_recoverSpeechEngine());
      return;
    }

    _scheduleSpeechRestart();
  }

  Future<void> _recoverSpeechEngine() async {
    if (_speechRecoveryInProgress || !mounted || _chatMode) return;
    _speechRecoveryInProgress = true;
    try {
      try {
        await _speechToText.stop();
      } catch (_) {}

      final available = await _speechToText.initialize(
        onStatus: _onSpeechStatus,
        onError: _onSpeechError,
      );

      if (!mounted) return;
      setState(() {
        _speechAvailable = available;
        _speechInitialized = available;
        if (!available) {
          _status = 'Speech recognition unavailable on device.';
        }
      });

      if (available && _alwaysListening && !_chatMode) {
        await Future.delayed(const Duration(milliseconds: 280));
        await _beginListening();
      }
    } catch (_) {
      _scheduleSpeechRestart();
    } finally {
      _speechRecoveryInProgress = false;
    }
  }

  void _onSpeechResult(stt.SpeechRecognitionResult result) {
    if (!mounted) return;
    final spoken = result.recognizedWords.trim();
    if (spoken.isEmpty) return;

    final normalized = spoken.toLowerCase();
    final wakeDetected =
        normalized.contains('hey aura') || normalized.contains('hi aura');

    setState(() {
      _transcribedText = spoken;
      if (wakeDetected) {
        _awaitingCommandAfterWake = true;
      }
    });

    if (!result.finalResult) return;

    final cleaned =
        spoken.replaceAll(RegExp(r'(?i)\b(hey aura|hi aura)\b'), '').trim();
    final shouldSend =
        cleaned.isNotEmpty &&
        (_awaitingCommandAfterWake || _manualListeningLatch);

    if (shouldSend) {
      setState(() {
        _awaitingCommandAfterWake = false;
        _manualListeningLatch = false;
      });
      _sendTextToBackend(cleaned);
    }
  }

  Future<void> _beginListening() async {
    if (!_speechAvailable ||
        !_speechInitialized ||
        _speechToText.isListening ||
        !_alwaysListening ||
        _chatMode) {
      return;
    }
    final now = DateTime.now();
    if (now.difference(_lastListenStart).inMilliseconds < 500) {
      return;
    }
    if (now.difference(_lastListenStop).inMilliseconds < 1200) {
      return;
    }

    try {
      _lastListenStart = now;
      await _speechToText.listen(
        onResult: _onSpeechResult,
        listenFor: const Duration(minutes: 30),
        pauseFor: const Duration(seconds: 12),
        partialResults: true,
        localeId: widget.language == 'ar' ? 'ar_EG' : 'en_US',
        listenMode: stt.ListenMode.dictation,
        cancelOnError: false,
      );
    } catch (_) {
      _scheduleSpeechRestart();
    }
  }

  Future<void> _connectThinkingSocket() async {
    await _wsSub?.cancel();
    await _wsChannel?.sink.close();

    final uri = Uri.parse(
      'ws://10.0.2.2:8000/ws/$_activeSessionId'
      '?user_id=$_activeUserId'
      '&device_id=${DeviceManager.deviceId}'
      '&platform=mobile',
    );
    _wsChannel = WebSocketChannel.connect(uri);
    _wsSub = _wsChannel!.stream.listen(
      (event) {
        try {
          final payload = jsonDecode(event as String) as Map<String, dynamic>;
          final type = (payload['type'] ?? '').toString();

          if (type == 'thinking_step') {
            final step = (payload['step'] ?? '').toString().trim();
            if (step.isNotEmpty && mounted) {
              setState(() {
                _isThinking = true;
                if (_thinkingSteps.isEmpty || _thinkingSteps.last != step) {
                  _thinkingSteps.add(step);
                  if (_thinkingSteps.length > 8) {
                    _thinkingSteps.removeAt(0);
                  }
                }
              });
            }
          } else if (type == 'thinking_clear') {
            if (mounted) {
              setState(() {
                _thinkingSteps.clear();
                _isThinking = false;
              });
            }
          } else if (type == 'confirmation_needed' ||
              type == 'clarification_needed') {
            final question =
                (payload['question'] ?? payload['text'] ?? '')
                    .toString()
                    .trim();
            if (mounted && question.isNotEmpty) {
              setState(() {
                _isThinking = false;
                _needsConfirmation = true;
                _confirmationRequest = question;
                _draftedMessage = (payload['full_content'] ?? '').toString();
                _responseText = question;
                _showExecutionWidget = true;
                _executionWidgetMinimized = false;
                _executionNeedsAttention = true;
                _executionWidgetTitle = 'Clarification needed';
                _executionWidgetSubtitle = _compactForWidget(question);
              });
            }
          }
        } catch (_) {}
      },
      onError: (_) {
        if (_alwaysListening) {
          Future.delayed(const Duration(seconds: 1), _connectThinkingSocket);
        }
      },
      onDone: () {
        if (_alwaysListening) {
          Future.delayed(const Duration(seconds: 1), _connectThinkingSocket);
        }
      },
      cancelOnError: false,
    );
  }

  Future<void> _minimizeToHomeForExecution() async {
    return;
  }

  Future<void> _restoreAppAfterExecution() async {
    return;
  }

  Future<void> _loadAccessibilityPref() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      _accessibilityEnabled = prefs.getBool('accessibilityEnabled') ?? false;
    });
    if (_accessibilityEnabled) {
      await _checkServiceStatus();
    }
  }

  Future<void> _saveAccessibilityPref(bool val) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('accessibilityEnabled', val);
    setState(() => _accessibilityEnabled = val);
    if (val) await _openAccessibilitySettings();
  }

  Future<void> _startActionServer() async {
    try {
      _actionServer = await HttpServer.bind('0.0.0.0', 9999);
      _actionServer!.listen((request) async {
        try {
          if (request.method == 'POST') {
            final body = await utf8.decoder.bind(request).join();
            final data = jsonDecode(body) as Map<String, dynamic>;
            if (data['action']?.toString().contains('START_AUTOMATION') ==
                true) {
              await _platform.invokeMethod('receiveBroadcast', data);
              if (data['action'].toString().contains('START_AUTOMATION')) {
                await _platform.invokeMethod('startAutomation');
              }
              request.response
                ..headers.contentType = ContentType.json
                ..write('{"success":true}');
            } else {
              final result = await DeviceManager.executeAction(data);
              request.response
                ..headers.contentType = ContentType.json
                ..write(jsonEncode(result));
            }
            await request.response.close();
          } else {
            request.response.statusCode = 405;
            await request.response.close();
          }
        } catch (e) {
          request.response.statusCode = 500;
          request.response.write('{"error":"${e.toString()}"}');
          await request.response.close();
        }
      });
    } catch (e) {
      debugPrint('[Server] start error: $e');
    }
  }

  Future<void> _registerWithBackend() async {
    if (await DeviceManager.registerDevice(
      userId: _activeUserId,
      sessionId: _activeSessionId,
      platform: 'mobile',
    )) {
      final t = await DeviceManager.getAccessibilityTree();
      if (t.isNotEmpty) await DeviceManager.sendUITree(t);
      Future.doWhile(() async {
        await Future.delayed(const Duration(seconds: 5));
        await DeviceManager.sendStatus();
        return true;
      });
    }
  }

  void _startPollingForActions() {
    Future.doWhile(() async {
      await Future.delayed(const Duration(seconds: 1));
      try {
        final r = await http.get(
          Uri.parse(
            '${DeviceManager.backendUrl}/device/${DeviceManager.deviceId}/pending-actions',
          ),
        );
        if (r.statusCode == 200) {
          final actions =
              ((jsonDecode(r.body) as Map)['actions'] as List?) ?? [];
          for (final a in actions) {
            await DeviceManager.executeAction(a as Map<String, dynamic>);
          }
        }
      } catch (_) {}
      return true;
    });
  }

  Future<void> _sendUITree() async {
    await DeviceManager.sendUITree({
      'screen_id': 'screen_main',
      'device_id': DeviceManager.deviceId,
      'app_name': 'AURA',
      'screen_name': 'Main Screen',
      'elements': [
        {'element_id': 1, 'type': 'button', 'text': 'Send', 'clickable': true},
        {
          'element_id': 2,
          'type': 'textfield',
          'text': _textCtrl.text,
          'clickable': true,
        },
      ],
      'timestamp': DateTime.now().millisecondsSinceEpoch / 1000,
    });
  }

  Future<void> _checkServiceStatus() async {
    try {
      final bool enabled = await _platform.invokeMethod('isServiceEnabled');
      setState(() {
        _serviceEnabled = enabled;
        _status =
            enabled
                ? 'Accessibility Service Enabled'
                : 'Please enable Accessibility Service';
      });
    } catch (e) {
      setState(
        () =>
            _status = _toUserFriendlyError(
              e.toString(),
              fallback: 'Unable to check accessibility status right now.',
            ),
      );
    }
  }

  void _setupMethodChannelListener() {
    _platform.setMethodCallHandler((call) async {
      if (call.method == 'onUITreeUpdate') return {'status': 'received'};
      return null;
    });
  }

  Future<void> _openAccessibilitySettings() async {
    try {
      await _platform.invokeMethod('openAccessibilitySettings');
    } catch (e) {
      setState(
        () =>
            _status = _toUserFriendlyError(
              e.toString(),
              fallback: 'Unable to open accessibility settings right now.',
            ),
      );
    }
  }

  Future<void> _sendTextToBackend(String text) async {
    if (text.trim().isEmpty) return;
    final hadPendingFollowup = _needsConfirmation;
    setState(() {
      _isLoading = true;
      _isExecuting = true; // REQ 14
      _isThinking = true;
      _thinkingSteps.clear();
      _status = 'Processing...';
      _responseText = '';
      _showExecutionWidget = false;
      _executionWidgetMinimized = true;
      _executionNeedsAttention = false;
      _executionWidgetTitle = 'Executing task';
      _executionWidgetSubtitle =
          hadPendingFollowup
              ? 'Applying your clarification...'
              : 'Processing your request...';
    });
    _thinkCtrl.repeat();
    try {
      await _sendUITree();
      final resp = await http.post(
        Uri.parse('${DeviceManager.backendUrl}/process'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'input': text,
          'session_id': _activeSessionId,
          'user_id': _activeUserId,
          'device_type': 'mobile',
          'user_language': widget.language,
        }),
      );
      final data = jsonDecode(resp.body) as Map<String, dynamic>;
      final extractedThinking = _extractThinkingSteps(data);
      setState(() {
        _isLoading = false;
        _isExecuting = false; // REQ 14
        _isThinking = false;
        if (extractedThinking.isNotEmpty) {
          _thinkingSteps
            ..clear()
            ..addAll(extractedThinking);
        }
        _thinkCtrl.stop();
        _thinkCtrl.reset();
        if (resp.statusCode == 200) {
          if (data['status'] == 'clarification_needed') {
            _status = 'Question:';
            _responseText = data['question'] ?? 'Clarification needed';
            _confirmationRequest =
                (data['question'] ?? data['clarification'] ?? '').toString();
            _draftedMessage =
                (data['draft_message'] ??
                        data['draft'] ??
                        data['message_to_send'] ??
                        '')
                    .toString();
            _needsConfirmation =
                _confirmationRequest.trim().isNotEmpty ||
                _draftedMessage.trim().isNotEmpty;
            _showExecutionWidget = false;
            _executionWidgetMinimized = true;
            _executionNeedsAttention = false;
            _executionWidgetTitle =
                _needsConfirmation ? 'Clarification needed' : 'Awaiting review';
            _executionWidgetSubtitle = _compactForWidget(
              _confirmationRequest.isNotEmpty
                  ? _confirmationRequest
                  : (_responseText.isNotEmpty
                      ? _responseText
                      : 'Please review and confirm to continue.'),
            );
          } else {
            _status = 'Done';
            _responseText =
                data['text'] ?? data['response'] ?? 'Task completed';
            _confirmationRequest = '';
            _draftedMessage = '';
            _needsConfirmation = false;
            _showExecutionWidget = false;
            _executionWidgetMinimized = true;
            _executionNeedsAttention = false;
            _executionWidgetTitle = 'Execution complete';
            _executionWidgetSubtitle = _compactForWidget(_responseText);
          }
          _textCtrl.clear();
          _transcribedText = '';
          _playTTSAudio(_responseText);
        } else {
          _status = 'Error';
          _responseText = _toUserFriendlyError(
            data['error']?.toString() ?? 'Unknown error',
            fallback: 'Task failed. Please try again.',
          );
          _showExecutionWidget = false;
          _executionWidgetMinimized = true;
          _executionNeedsAttention = false;
          _executionWidgetTitle = 'Execution failed';
          _executionWidgetSubtitle = _compactForWidget(_responseText);
        }
      });
    } catch (e) {
      setState(() {
        _isLoading = false;
        _isExecuting = false;
        _isThinking = false;
        _thinkCtrl.stop();
        _thinkCtrl.reset();
        _status = 'Error';
        _responseText = _toUserFriendlyError(
          e.toString(),
          fallback: 'Something went wrong while processing your request.',
        );
        _confirmationRequest = '';
        _draftedMessage = '';
        _needsConfirmation = false;
        _showExecutionWidget = false;
        _executionWidgetMinimized = true;
        _executionNeedsAttention = false;
        _executionWidgetTitle = 'Execution failed';
        _executionWidgetSubtitle = _compactForWidget(_responseText);
      });
    }
  }

  void _scheduleExecutionWidgetDismiss() {
    final token = ++_executionDismissToken;
    Future.delayed(const Duration(milliseconds: 1600), () {
      if (!mounted || token != _executionDismissToken) return;
      if (_needsConfirmation || _isPaused) return;
      setState(() {
        _showExecutionWidget = false;
        _executionWidgetMinimized = true;
        _executionNeedsAttention = false;
      });
    });
  }

  // REQ 16: pause execution
  Future<void> _pauseExecution() async {
    setState(() {
      _isPaused = true;
      _showExecutionWidget = true;
      _executionWidgetTitle = 'Execution paused';
      _executionWidgetSubtitle = 'Tap play to continue';
      _executionNeedsAttention = false;
    });
    try {
      await _platform.invokeMethod('pauseExecution');
    } catch (_) {}
  }

  // REQ 16: resume execution
  Future<void> _resumeExecution() async {
    setState(() {
      _isPaused = false;
      _showExecutionWidget = true;
      _executionWidgetTitle = 'Executing task';
      _executionWidgetSubtitle = 'AURA is running in background';
      _executionNeedsAttention = false;
      _executionWidgetMinimized = true;
    });
    try {
      await _platform.invokeMethod('resumeExecution');
    } catch (_) {}
  }

  // REQ 16: stop execution
  Future<void> _stopExecution() async {
    setState(() {
      _isPaused = false;
      _isExecuting = false;
      _isLoading = false;
      _isThinking = false;
      _status = 'Stopped';
      _showExecutionWidget = true;
      _executionWidgetMinimized = false;
      _executionNeedsAttention = true;
      _executionWidgetTitle = 'Execution stopped';
      _executionWidgetSubtitle = 'Automation was stopped manually';
    });
    _thinkCtrl.stop();
    _thinkCtrl.reset();
    try {
      await _platform.invokeMethod('stopExecution');
    } catch (_) {}
  }

  Future<void> _playTTSAudio(String text) async {
    if (text.trim().isEmpty) return;
    try {
      await _flutterTts.stop();
      await _flutterTts.speak(text);
    } catch (_) {}
  }

  Future<void> _toggleRecording() async {
    if (!_speechAvailable) {
      setState(() {
        _status = 'Speech recognition is unavailable on this device.';
      });
      return;
    }

    if (_chatMode) {
      setState(() {
        _chatMode = false;
        _manualListeningLatch = true;
        _awaitingCommandAfterWake = true;
        _status = 'Listening for your command...';
      });
      await _beginListening();
      return;
    }

    setState(() {
      _manualListeningLatch = !_manualListeningLatch;
      _awaitingCommandAfterWake = _manualListeningLatch;
      _status =
          _manualListeningLatch
              ? 'Listening for your command...'
              : 'Voice control idle.';
    });

    if (_manualListeningLatch) {
      await _beginListening();
    }
  }

  Future<void> _openChatViewer(String sessionId, String title) async {
    setState(() {
      _viewingSessionId = sessionId;
      _viewingTitle = title;
      _viewingMessages = [];
      _viewingLoading = true;
    });
    try {
      final r = await http.get(
        Uri.parse(
          '${DeviceManager.backendUrl}/chat-messages/$sessionId?user_id=$_activeUserId',
        ),
      );
      if (r.statusCode == 200) {
        final data = jsonDecode(r.body) as Map<String, dynamic>;
        setState(() {
          _viewingMessages = List<Map<String, dynamic>>.from(
            data['messages'] ?? [],
          );
        });
      }
    } catch (_) {}
    setState(() => _viewingLoading = false);
  }

  Future<void> _loadChats() async {
    setState(() => _chatsLoading = true);
    try {
      final r = await http.get(
        Uri.parse('${DeviceManager.backendUrl}/chats/$_activeUserId'),
      );
      if (r.statusCode == 200) {
        final data = jsonDecode(r.body) as Map<String, dynamic>;
        setState(() {
          _chats = List<Map<String, dynamic>>.from(data['chats'] ?? []);
        });
      }
    } catch (_) {}
    setState(() => _chatsLoading = false);
  }

  Future<void> _deleteChat(String sessionId) async {
    try {
      final r = await http.delete(
        Uri.parse(
          '${DeviceManager.backendUrl}/chats/$sessionId?user_id=$_activeUserId',
        ),
      );
      if (r.statusCode == 200) {
        setState(() {
          _chats.removeWhere((c) => c['session_id'] == sessionId);
        });
      }
    } catch (_) {}
  }

  Future<void> _loadProfile() async {
    try {
      final r = await http.get(
        Uri.parse(
          '${DeviceManager.backendUrl}/user/profile?user_id=$_activeUserId',
        ),
      );
      if (r.statusCode == 200) {
        final data = jsonDecode(r.body) as Map<String, dynamic>;
        _usernameSettingsCtrl.text = data['username'] ?? _userName;
        _emailSettingsCtrl.text = data['email'] ?? '';
      }
    } catch (_) {}
  }

  Future<void> _saveProfile() async {
    setState(() {
      _profileSaving = true;
      _profileSaveStatus = null;
    });
    try {
      final r = await http.put(
        Uri.parse('${DeviceManager.backendUrl}/user/profile'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'user_id': _activeUserId,
          'username': _usernameSettingsCtrl.text.trim(),
          'email': _emailSettingsCtrl.text.trim(),
        }),
      );
      if (r.statusCode == 200) {
        setState(() {
          _userName = _usernameSettingsCtrl.text.trim();
          _profileSaveStatus = '✓ Saved';
        });
      } else {
        final err = jsonDecode(r.body);
        setState(() => _profileSaveStatus = err['detail'] ?? 'Save failed');
      }
    } catch (e) {
      setState(() => _profileSaveStatus = 'Error: $e');
    }
    setState(() => _profileSaving = false);
  }

  // REQ 10: logout
  Future<void> _logout() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.clear();
    if (mounted) {
      Navigator.of(context).pushAndRemoveUntil(
        PageRouteBuilder(
          pageBuilder: (_, __, ___) => const StartupScreen(),
          transitionsBuilder:
              (_, a, __, child) => FadeTransition(opacity: a, child: child),
          transitionDuration: const Duration(milliseconds: 500),
        ),
        (_) => false,
      );
    }
  }

  String _greeting() {
    final h = DateTime.now().hour;
    if (h >= 5 && h < 9) return 'Rise and shine, $_userName!';
    if (h >= 9 && h < 12) return 'Good morning, $_userName!';
    if (h >= 12 && h < 15) return 'Lunchtime, $_userName?';
    if (h >= 15 && h < 18) return 'Good afternoon, $_userName!';
    if (h >= 18 && h < 21) return 'Evening, $_userName!';
    return 'Hello night owl, $_userName!';
  }

  String _contextualHeadline() {
    final h = DateTime.now().hour;
    if (h >= 5 && h < 12) {
      const opts = [
        'What would you like to achieve today?',
        "Let's get a head start.",
        'Ready to tackle the day?',
      ];
      return opts[DateTime.now().minute % opts.length];
    } else if (h >= 12 && h < 18) {
      const opts = [
        'How is your day going?',
        "Let's keep the momentum going.",
        'Need help with anything this afternoon?',
      ];
      return opts[DateTime.now().minute % opts.length];
    } else {
      const opts = [
        'Winding down?',
        "Let's wrap up for the day.",
        'Your assistant is ready for the evening.',
      ];
      return opts[DateTime.now().minute % opts.length];
    }
  }

  String _compactForWidget(String raw) {
    final oneLine = raw.replaceAll(RegExp(r'\s+'), ' ').trim();
    if (oneLine.length <= 80) return oneLine;
    return '${oneLine.substring(0, 77)}...';
  }

  List<String> _extractThinkingSteps(Map<String, dynamic> data) {
    final raw =
        data['thinking_steps'] ??
        data['thinking'] ??
        data['steps'] ??
        data['reasoning_steps'] ??
        (data['meta'] is Map<String, dynamic>
            ? (data['meta'] as Map<String, dynamic>)['thinking_steps']
            : null);

    if (raw is List) {
      return raw
          .map((e) {
            if (e is Map<String, dynamic>) {
              return (e['step'] ?? e['text'] ?? '').toString().trim();
            }
            return e.toString().trim();
          })
          .where((s) => s.isNotEmpty)
          .toList();
    }

    if (raw is String && raw.trim().isNotEmpty) {
      return [raw.trim()];
    }
    return const [];
  }

  String _toUserFriendlyError(
    String raw, {
    String fallback = 'Something went wrong. Please try again.',
  }) {
    final message = raw.trim();
    final lower = message.toLowerCase();

    if (lower.contains('websocket') ||
        lower.contains('socket') ||
        lower.contains('connection refused') ||
        lower.contains('failed host lookup') ||
        lower.contains('network')) {
      return 'Connection issue. Please check your network and try again.';
    }
    if (lower.contains('timeout')) {
      return 'The request timed out. Please try again.';
    }
    if (lower.contains('permission') || lower.contains('denied')) {
      return 'Permission is required to continue this action.';
    }
    if (lower.contains('exception:')) {
      return fallback;
    }
    if (message.isEmpty) {
      return fallback;
    }
    return message.length > 180 ? fallback : message;
  }

  @override
  void dispose() {
    _alwaysListening = false;
    _textCtrl.dispose();
    _usernameSettingsCtrl.dispose();
    _emailSettingsCtrl.dispose();
    _wsSub?.cancel();
    _wsChannel?.sink.close();
    _speechToText.stop();
    _flutterTts.stop();
    _actionServer?.close();
    _pulseCtrl.dispose();
    _thinkCtrl.dispose();
    _waveCtrl.dispose();
    _videoCtrl.dispose();
    super.dispose();
  }

  // ── Build ──────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        children: [
          if (_videoCtrl.value.isInitialized)
            SizedBox.expand(
              child: FittedBox(
                fit: BoxFit.cover,
                child: SizedBox(
                  width: _videoCtrl.value.size.width,
                  height: _videoCtrl.value.size.height,
                  child: VideoPlayer(_videoCtrl),
                ),
              ),
            ),

          Positioned.fill(
            child: IgnorePointer(
              child: Container(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [
                      Colors.black.withOpacity(0.16),
                      Colors.black.withOpacity(0.44),
                    ],
                  ),
                ),
              ),
            ),
          ),

          Positioned.fill(
            child: IgnorePointer(
              child: Center(
                child: Opacity(
                  opacity: 0.18,
                  child: Image.asset(
                    'assets/aura_icon_haze.png',
                    width: 420,
                    height: 420,
                    fit: BoxFit.contain,
                  ),
                ),
              ),
            ),
          ),

          SafeArea(
            child: Stack(
              children: [
                Column(
                  children: [
                    _buildHeader(),
                    Expanded(
                      child: _chatMode ? _buildChatMode() : _buildVoiceMode(),
                    ),
                  ],
                ),
              ],
            ),
          ),

          _buildSidebar(),
          if (_showSettings) _buildSettingsModal(),
          if (_viewingSessionId != null) _buildChatViewerModal(),
        ],
      ),
    );
  }

  // REQ 14: compact execution widget
  Widget _buildExecutionWidget() {
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: Stack(
        children: [
          if (_videoCtrl.value.isInitialized)
            SizedBox.expand(
              child: FittedBox(
                fit: BoxFit.cover,
                child: SizedBox(
                  width: _videoCtrl.value.size.width,
                  height: _videoCtrl.value.size.height,
                  child: VideoPlayer(_videoCtrl),
                ),
              ),
            ),
          Align(
            alignment: Alignment.bottomCenter,
            child: SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(32),
                  child: BackdropFilter(
                    filter: ImageFilter.blur(sigmaX: 30, sigmaY: 30),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 20,
                        vertical: 16,
                      ),
                      decoration: BoxDecoration(
                        color: Colors.black.withOpacity(0.55),
                        borderRadius: BorderRadius.circular(32),
                        border: Border.all(
                          color: AuraTheme.pink400.withOpacity(0.35),
                          width: 1.2,
                        ),
                      ),
                      child: Row(
                        children: [
                          // REQ 6: mini wave visualizer
                          AnimatedBuilder(
                            animation: _waveCtrl,
                            builder:
                                (_, __) => Row(
                                  children: List.generate(5, (i) {
                                    final h =
                                        8.0 +
                                        sin((i + _waveCtrl.value * 10) * 0.8) *
                                            10;
                                    return Container(
                                      margin: const EdgeInsets.symmetric(
                                        horizontal: 2,
                                      ),
                                      width: 3,
                                      height: h.clamp(4, 24),
                                      decoration: BoxDecoration(
                                        color: AuraTheme.pink400,
                                        borderRadius: BorderRadius.circular(2),
                                      ),
                                    );
                                  }),
                                ),
                          ),
                          const SizedBox(width: 14),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Text(
                                  _isPaused ? 'Paused' : 'Executing...',
                                  style: _f(
                                    AuraTheme.pink300,
                                    size: 13,
                                    weight: FontWeight.w600,
                                  ),
                                ),
                                Text(
                                  _status,
                                  style: _f(AuraTheme.textSecondary, size: 11),
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ],
                            ),
                          ),
                          // REQ 16: pause/resume/stop
                          _execBtn(
                            icon:
                                _isPaused
                                    ? Icons.play_arrow_rounded
                                    : Icons.pause_rounded,
                            onTap:
                                _isPaused ? _resumeExecution : _pauseExecution,
                          ),
                          const SizedBox(width: 8),
                          _execBtn(
                            icon: Icons.stop_rounded,
                            onTap: _stopExecution,
                            color: Colors.redAccent,
                          ),
                          const SizedBox(width: 8),
                          _execBtn(
                            icon: Icons.open_in_full_rounded,
                            onTap: () => setState(() => _isExecuting = false),
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

  Widget _execBtn({
    required IconData icon,
    required VoidCallback onTap,
    Color? color,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(8),
        decoration: BoxDecoration(
          color: (color ?? Colors.white).withOpacity(0.1),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: (color ?? Colors.white).withOpacity(0.2)),
        ),
        child: Icon(icon, color: color ?? Colors.white, size: 18),
      ),
    );
  }

  Widget _buildChatMode() {
    return Column(
      children: [
        // Chat content area with frosted glass
        Expanded(
          child: Container(
            margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(24),
              border: Border.all(
                color: Colors.white.withOpacity(0.08),
                width: 1,
              ),
            ),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(24),
              child: BackdropFilter(
                filter: ImageFilter.blur(sigmaX: 20, sigmaY: 20),
                child: Container(
                  color: AuraTheme.bgSurface.withOpacity(0.28),
                  padding: const EdgeInsets.all(20),
                  child: ListView(
                    physics: const BouncingScrollPhysics(),
                    children: [
                      if (_transcribedText.isEmpty &&
                          _responseText.isEmpty &&
                          !_isThinking) ...[
                        Text(
                          _greeting(),
                          style: _f(
                            AuraTheme.textSecondary,
                            size: 12,
                            weight: FontWeight.w600,
                          ),
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 10),
                      ],
                      if (_transcribedText.isEmpty &&
                          _responseText.isEmpty) ...[
                        const SizedBox(height: 20),
                        Text(
                          _contextualHeadline(),
                          style: _f(
                            AuraTheme.textPrimary,
                            size: 22,
                            weight: FontWeight.w600,
                            height: 1.3,
                          ),
                          textAlign: TextAlign.center,
                        ),
                      ],

                      if (_transcribedText.isNotEmpty) ...[
                        _chatBubble(_transcribedText, isUser: true),
                        const SizedBox(height: 12),
                      ],

                      if (_isThinking) ...[
                        _thinkingIndicator(),
                        const SizedBox(height: 12),
                      ],

                      if (_thinkingSteps.isNotEmpty) ...[
                        Container(
                          width: double.infinity,
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            color: Colors.white.withOpacity(0.04),
                            borderRadius: BorderRadius.circular(14),
                            border: Border.all(
                              color: Colors.white.withOpacity(0.08),
                            ),
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Thinking steps',
                                style: _f(
                                  AuraTheme.pink300,
                                  size: 11,
                                  weight: FontWeight.w700,
                                  spacing: 0.8,
                                ),
                              ),
                              const SizedBox(height: 8),
                              ..._thinkingSteps.map(
                                (step) => Padding(
                                  padding: const EdgeInsets.only(bottom: 6),
                                  child: Row(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        '• ',
                                        style: _f(
                                          AuraTheme.textSecondary,
                                          size: 12,
                                        ),
                                      ),
                                      Expanded(
                                        child: Text(
                                          step,
                                          style: _f(
                                            AuraTheme.textSecondary,
                                            size: 12,
                                            height: 1.4,
                                          ),
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(height: 12),
                      ],

                      if (_responseText.isNotEmpty && !_isThinking) ...[
                        _chatBubble(_responseText, isUser: false),
                      ],
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),

        // Text Entry Field
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 20),
          child: _buildTextInputBar(),
        ),
      ],
    );
  }

  // Shared header for both voice and text modes.
  Widget _buildHeader() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 14, 20, 10),
      child: Row(
        children: [
          GestureDetector(
            onTap: () => setState(() => _isSidebarOpen = true),
            child: _iconBtn(Icons.menu_rounded),
          ),
          Expanded(
            child: Column(
              children: [
                Text(
                  'AURA',
                  style: _f(
                    AuraTheme.textPrimary,
                    size: 16,
                    weight: FontWeight.w700,
                    spacing: 2,
                  ),
                ),
              ],
            ),
          ),
          GestureDetector(
            onTap: () => setState(() => _showSettings = true),
            child: _iconBtn(Icons.settings_rounded),
          ),
        ],
      ),
    );
  }

  Widget _iconBtn(IconData icon) => Container(
    padding: const EdgeInsets.all(9),
    decoration: BoxDecoration(
      color: Colors.white.withOpacity(0.06),
      borderRadius: BorderRadius.circular(12),
      border: Border.all(color: Colors.white.withOpacity(0.08)),
    ),
    child: Icon(icon, color: AuraTheme.textSecondary, size: 20),
  );

  Widget _buildVoiceMode() {
    final liveText =
        _needsConfirmation
            ? _confirmationRequest.trim()
            : (_isThinking
                ? 'Processing the current task...'
                : (_status.isNotEmpty
                    ? _status
                    : 'Ready for your next command.'));
    final transcriptText =
        _transcribedText.isNotEmpty
            ? _transcribedText
            : (_isRecording ? 'Listening for your request...' : '');
    final resultText =
        _responseText.isNotEmpty
            ? _responseText
            : (_draftedMessage.isNotEmpty ? _draftedMessage : '');
    final wakeDetected = _transcribedText.toLowerCase().contains('hey aura');
    final listening = _isRecording || wakeDetected;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 18),
      child: Column(
        children: [
          Expanded(
            flex: 3,
            child: _buildVoiceGlassContainer(
              borderRadius: 28,
              sigma: 10,
              padding: const EdgeInsets.fromLTRB(18, 16, 18, 16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(
                        _isThinking
                            ? Icons.auto_awesome_rounded
                            : (_needsConfirmation
                                ? Icons.fact_check_rounded
                                : Icons.notes_rounded),
                        color: AuraTheme.pink300,
                        size: 16,
                      ),
                      const SizedBox(width: 8),
                      Text(
                        _needsConfirmation ? 'Live Review' : 'Live Context',
                        style: _f(
                          AuraTheme.pink300,
                          size: 11,
                          weight: FontWeight.w700,
                          spacing: 1,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Expanded(
                    child: SingleChildScrollView(
                      physics: const BouncingScrollPhysics(),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          TypewriterText(
                            text: liveText,
                            style: _f(
                              AuraTheme.textPrimary,
                              size: 19,
                              height: 1.42,
                            ),
                            maxLines: 30,
                          ),
                          if (transcriptText.isNotEmpty) ...[
                            const SizedBox(height: 16),
                            Text(
                              'Transcription',
                              style: _f(
                                AuraTheme.textSecondary,
                                size: 10,
                                weight: FontWeight.w700,
                                spacing: 1,
                              ),
                            ),
                            const SizedBox(height: 6),
                            TypewriterText(
                              text: transcriptText,
                              style: _f(
                                AuraTheme.textPrimary,
                                size: 15,
                                height: 1.5,
                              ),
                              maxLines: 30,
                            ),
                          ],
                          if (resultText.isNotEmpty) ...[
                            const SizedBox(height: 16),
                            Text(
                              'Result',
                              style: _f(
                                AuraTheme.textSecondary,
                                size: 10,
                                weight: FontWeight.w700,
                                spacing: 1,
                              ),
                            ),
                            const SizedBox(height: 6),
                            TypewriterText(
                              text: resultText,
                              style: _f(
                                AuraTheme.textPrimary,
                                size: 15,
                                height: 1.5,
                              ),
                              maxLines: 30,
                            ),
                          ],
                          if (_thinkingSteps.isNotEmpty) ...[
                            const SizedBox(height: 16),
                            Text(
                              'Thinking Steps',
                              style: _f(
                                AuraTheme.textSecondary,
                                size: 10,
                                weight: FontWeight.w700,
                                spacing: 1,
                              ),
                            ),
                            const SizedBox(height: 6),
                            ..._thinkingSteps.map(
                              (step) => Padding(
                                padding: const EdgeInsets.only(bottom: 6),
                                child: TypewriterText(
                                  text: '• $step',
                                  style: _f(
                                    AuraTheme.textSecondary,
                                    size: 13,
                                    height: 1.45,
                                  ),
                                  maxLines: 4,
                                ),
                              ),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          Expanded(
            flex: 2,
            child: _buildVoiceGlassContainer(
              borderRadius: 34,
              sigma: 9,
              active: listening,
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(
                    listening ? 'Listening' : 'Voice Control',
                    style: _f(
                      listening ? AuraTheme.pink300 : AuraTheme.textSecondary,
                      size: 12,
                      weight: FontWeight.w700,
                      spacing: 1,
                    ),
                  ),
                  const SizedBox(height: 12),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                    children: [
                      _voiceControlButton(
                        icon: Icons.close_rounded,
                        onTap: () {
                          setState(() {
                            _isRecording = false;
                            _transcribedText = '';
                            _chatMode = true;
                            _isSidebarOpen = false;
                          });
                          _speechToText.stop();
                          _lastListenStop = DateTime.now();
                          _pulseCtrl.stop();
                          _pulseCtrl.reset();
                        },
                        color: AuraTheme.textSecondary,
                      ),
                      AnimatedBuilder(
                        animation: _pulseCtrl,
                        builder: (_, __) {
                          final glow =
                              listening
                                  ? (0.18 + (_pulseCtrl.value * 0.34))
                                  : 0.0;
                          return _voiceControlButton(
                            icon:
                                _isRecording
                                    ? Icons.graphic_eq_rounded
                                    : Icons.mic_rounded,
                            onTap: _toggleRecording,
                            size: 74,
                            gradient:
                                listening
                                    ? const LinearGradient(
                                      colors: [
                                        AuraTheme.pink400,
                                        AuraTheme.pink700,
                                      ],
                                      begin: Alignment.topLeft,
                                      end: Alignment.bottomRight,
                                    )
                                    : null,
                            color:
                                listening
                                    ? Colors.white
                                    : AuraTheme.textPrimary,
                            borderColor:
                                listening
                                    ? AuraTheme.pink300.withOpacity(0.42)
                                    : Colors.white.withOpacity(0.12),
                            boxShadow: [
                              if (listening)
                                BoxShadow(
                                  color: AuraTheme.pink400.withOpacity(glow),
                                  blurRadius: 22,
                                  spreadRadius: 6,
                                ),
                            ],
                          );
                        },
                      ),
                      _voiceControlButton(
                        icon: Icons.settings_rounded,
                        onTap: () => setState(() => _showSettings = true),
                        color: AuraTheme.textSecondary,
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Text(
                    listening
                        ? 'Listening for your command...'
                        : 'Say "hey aura" or tap the mic.',
                    style: _f(AuraTheme.textMuted, size: 11, height: 1.35),
                    textAlign: TextAlign.center,
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _voiceControlButton({
    required IconData icon,
    required VoidCallback onTap,
    double size = 52,
    Color? color,
    Color? borderColor,
    Gradient? gradient,
    List<BoxShadow>? boxShadow,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: gradient,
          color: gradient == null ? Colors.white.withOpacity(0.05) : null,
          border: Border.all(
            color: borderColor ?? Colors.white.withOpacity(0.1),
          ),
          boxShadow: boxShadow,
        ),
        child: Icon(icon, color: color ?? Colors.white, size: size * 0.44),
      ),
    );
  }

  // REQ 12: chat bubbles styled like desktop theme
  Widget _chatBubble(String text, {required bool isUser}) {
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.75,
        ),
        margin: EdgeInsets.only(
          top: 4,
          bottom: 4,
          left: isUser ? 40 : 0,
          right: isUser ? 0 : 40,
        ),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color:
              isUser
                  ? AuraTheme.pink500.withOpacity(0.85)
                  : Colors.white.withOpacity(0.08),
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(18),
            topRight: const Radius.circular(18),
            bottomLeft: Radius.circular(isUser ? 18 : 4),
            bottomRight: Radius.circular(isUser ? 4 : 18),
          ),
          border: Border.all(
            color:
                isUser
                    ? AuraTheme.pink400.withOpacity(0.4)
                    : Colors.white.withOpacity(0.1),
          ),
          boxShadow:
              isUser
                  ? [
                    BoxShadow(
                      color: AuraTheme.pink500.withOpacity(0.2),
                      blurRadius: 12,
                      offset: const Offset(0, 4),
                    ),
                  ]
                  : [],
        ),
        child: Text(text, style: _f(Colors.white, size: 14, height: 1.5)),
      ),
    );
  }

  Widget _thinkingIndicator() {
    return Align(
      alignment: Alignment.centerLeft,
      child: AnimatedBuilder(
        animation: _thinkCtrl,
        builder:
            (_, __) => Opacity(
              opacity: 0.5 + (_thinkCtrl.value * 0.5),
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 14,
                  vertical: 10,
                ),
                decoration: BoxDecoration(
                  color: Colors.white.withOpacity(0.06),
                  borderRadius: const BorderRadius.only(
                    topLeft: Radius.circular(18),
                    topRight: Radius.circular(18),
                    bottomRight: Radius.circular(18),
                    bottomLeft: Radius.circular(4),
                  ),
                  border: Border.all(color: Colors.white.withOpacity(0.08)),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(
                      Icons.auto_awesome,
                      color: AuraTheme.pink400,
                      size: 14,
                    ),
                    const SizedBox(width: 8),
                    Text(
                      'Thinking...',
                      style: _f(
                        AuraTheme.pink300,
                        size: 13,
                        weight: FontWeight.w500,
                      ),
                    ),
                  ],
                ),
              ),
            ),
      ),
    );
  }

  // REQ 16: execution bar inline in chat
  Widget _buildExecutionBar() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(20),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 20, sigmaY: 20),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            decoration: BoxDecoration(
              color: Colors.black.withOpacity(0.4),
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: AuraTheme.pink400.withOpacity(0.3)),
            ),
            child: Row(
              children: [
                AnimatedBuilder(
                  animation: _waveCtrl,
                  builder:
                      (_, __) => Row(
                        children: List.generate(4, (i) {
                          final h =
                              6.0 + sin((i + _waveCtrl.value * 8) * 0.9) * 8;
                          return Container(
                            margin: const EdgeInsets.symmetric(horizontal: 1.5),
                            width: 3,
                            height: h.clamp(3, 20),
                            decoration: BoxDecoration(
                              color: AuraTheme.pink400,
                              borderRadius: BorderRadius.circular(2),
                            ),
                          );
                        }),
                      ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    _isPaused ? 'Paused' : 'Executing task...',
                    style: _f(
                      AuraTheme.pink300,
                      size: 12,
                      weight: FontWeight.w600,
                    ),
                  ),
                ),
                _execBtn(
                  icon:
                      _isPaused
                          ? Icons.play_arrow_rounded
                          : Icons.pause_rounded,
                  onTap: _isPaused ? _resumeExecution : _pauseExecution,
                ),
                const SizedBox(width: 6),
                _execBtn(
                  icon: Icons.stop_rounded,
                  onTap: _stopExecution,
                  color: Colors.redAccent,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildTextInputBar() {
    final voiceActive =
        _isRecording || _manualListeningLatch || _awaitingCommandAfterWake;
    return ClipRRect(
      borderRadius: BorderRadius.circular(30),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 15, sigmaY: 15),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          decoration: BoxDecoration(
            color: Colors.black.withOpacity(0.45),
            borderRadius: BorderRadius.circular(30),
            border: Border.all(
              color:
                  voiceActive
                      ? AuraTheme.pink400.withOpacity(0.65)
                      : Colors.white.withOpacity(0.1),
              width: voiceActive ? 1.4 : 1,
            ),
            boxShadow: [
              if (voiceActive)
                BoxShadow(
                  color: AuraTheme.pink400.withOpacity(0.22),
                  blurRadius: 20,
                  spreadRadius: 1,
                ),
            ],
          ),
          child: Row(
            children: [
              const SizedBox(width: 8),
              Expanded(
                child: TextField(
                  controller: _textCtrl,
                  style: _f(AuraTheme.textPrimary, size: 15),
                  decoration: InputDecoration(
                    hintText: 'Type your command...',
                    hintStyle: _f(AuraTheme.textMuted, size: 14),
                    border: InputBorder.none,
                  ),
                  textInputAction: TextInputAction.send,
                  onSubmitted: _sendTextToBackend,
                ),
              ),
              GestureDetector(
                onTap: _toggleRecording,
                child: Container(
                  margin: const EdgeInsets.only(right: 8),
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color:
                        voiceActive
                            ? AuraTheme.pink500.withOpacity(0.85)
                            : Colors.white.withOpacity(0.09),
                    shape: BoxShape.circle,
                    border: Border.all(
                      color:
                          voiceActive
                              ? AuraTheme.pink300.withOpacity(0.7)
                              : Colors.white.withOpacity(0.15),
                    ),
                  ),
                  child: Icon(
                    voiceActive ? Icons.graphic_eq_rounded : Icons.mic_rounded,
                    color: Colors.white,
                    size: 18,
                  ),
                ),
              ),
              GestureDetector(
                onTap:
                    _isLoading
                        ? null
                        : () => _sendTextToBackend(_textCtrl.text),
                child: Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: AuraTheme.pink500.withOpacity(
                      _isLoading ? 0.35 : 0.85,
                    ),
                    shape: BoxShape.circle,
                    boxShadow: [
                      BoxShadow(
                        color: AuraTheme.pink500.withOpacity(0.24),
                        blurRadius: 12,
                        offset: const Offset(0, 3),
                      ),
                    ],
                  ),
                  child: Icon(
                    _isLoading
                        ? Icons.hourglass_top_rounded
                        : Icons.send_rounded,
                    color: Colors.white,
                    size: 18,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildMiddleContextPanel() {
    final String fallback = _responseText.isNotEmpty ? _responseText : _status;
    final String confirmation = _confirmationRequest.trim();
    final String draft = _draftedMessage.trim();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(
              _needsConfirmation
                  ? Icons.fact_check_rounded
                  : Icons.chat_bubble_outline_rounded,
              color: AuraTheme.pink300,
              size: 16,
            ),
            const SizedBox(width: 8),
            Text(
              _needsConfirmation ? 'Confirmation Needed' : 'Live Context',
              style: _f(
                AuraTheme.pink300,
                size: 12,
                weight: FontWeight.w700,
                spacing: 0.6,
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(18),
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 14, sigmaY: 14),
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.white.withOpacity(0.03),
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: Colors.white.withOpacity(0.1)),
                ),
                child: SingleChildScrollView(
                  physics: const BouncingScrollPhysics(),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (confirmation.isNotEmpty) ...[
                        Text(
                          'Request',
                          style: _f(
                            AuraTheme.textSecondary,
                            size: 11,
                            weight: FontWeight.w700,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          confirmation,
                          style: _f(
                            AuraTheme.textPrimary,
                            size: 13,
                            height: 1.45,
                          ),
                        ),
                        const SizedBox(height: 10),
                      ],
                      if (draft.isNotEmpty) ...[
                        Text(
                          'Draft Message',
                          style: _f(
                            AuraTheme.textSecondary,
                            size: 11,
                            weight: FontWeight.w700,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          draft,
                          style: _f(
                            AuraTheme.textPrimary,
                            size: 13,
                            height: 1.45,
                          ),
                        ),
                      ],
                      if (confirmation.isEmpty && draft.isEmpty)
                        Text(
                          fallback,
                          style: _f(
                            AuraTheme.textPrimary,
                            size: 13,
                            height: 1.45,
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
    );
  }

  Widget _buildVoiceGlassContainer({
    required Widget child,
    double borderRadius = 24,
    double sigma = 9,
    bool active = false,
    EdgeInsetsGeometry padding = const EdgeInsets.all(18),
  }) {
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        border: Border.all(
          color:
              active
                  ? AuraTheme.pink400.withOpacity(0.58)
                  : Colors.white.withOpacity(0.07),
          width: active ? 1.3 : 1,
        ),
        borderRadius: BorderRadius.circular(borderRadius),
        boxShadow: [
          if (active)
            BoxShadow(
              color: AuraTheme.pink400.withOpacity(0.22),
              blurRadius: 24,
              spreadRadius: 2,
            ),
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(borderRadius),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: sigma, sigmaY: sigma),
          child: Container(
            padding: padding,
            color: AuraTheme.bgSurface.withOpacity(0.16),
            child: child,
          ),
        ),
      ),
    );
  }

  Widget _buildHeaderBadge(String label, {Color? color}) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
    decoration: BoxDecoration(
      color: (color ?? Colors.white).withOpacity(0.06),
      borderRadius: BorderRadius.circular(999),
      border: Border.all(color: (color ?? Colors.white).withOpacity(0.08)),
    ),
    child: Text(
      label,
      style: _f(
        (color ?? Colors.white).withOpacity(0.78),
        size: 10,
        weight: FontWeight.w700,
        spacing: 0.9,
      ),
    ),
  );

  Widget _glassButton({
    required IconData icon,
    required String label,
    required VoidCallback onTap,
    Color? color,
  }) => GestureDetector(
    onTap: onTap,
    child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: (color ?? Colors.white).withOpacity(0.06),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: (color ?? Colors.white).withOpacity(0.12),
          width: 1.2,
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            icon,
            color: (color ?? Colors.white).withOpacity(0.85),
            size: 17,
          ),
          const SizedBox(width: 8),
          Text(
            label,
            style: _f(
              (color ?? Colors.white).withOpacity(0.85),
              size: 13,
              weight: FontWeight.w500,
            ),
          ),
        ],
      ),
    ),
  );

  // ── Sidebar ────────────────────────────────────────────────────────────────

  Widget _buildSidebar() {
    return Stack(
      children: [
        if (_isSidebarOpen)
          Positioned.fill(
            child: GestureDetector(
              onTap: () => setState(() => _isSidebarOpen = false),
              child: Container(color: Colors.black.withOpacity(0.4)),
            ),
          ),
        AnimatedPositioned(
          duration: const Duration(milliseconds: 280),
          curve: Curves.easeInOutCubic,
          left: _isSidebarOpen ? 0 : -290,
          top: 0,
          bottom: 0,
          child: ClipRRect(
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 40, sigmaY: 40),
              child: Container(
                width: 280,
                decoration: BoxDecoration(
                  color: Colors.black.withOpacity(0.6),
                  border: Border(
                    right: BorderSide(color: Colors.white.withOpacity(0.08)),
                  ),
                ),
                child: SafeArea(
                  child: Column(
                    children: [
                      // Header
                      Padding(
                        padding: const EdgeInsets.fromLTRB(20, 20, 20, 8),
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text(
                              'AURA',
                              style: _f(
                                Colors.white,
                                weight: FontWeight.w700,
                                size: 17,
                                spacing: 3,
                              ),
                            ),
                            GestureDetector(
                              onTap:
                                  () => setState(() => _isSidebarOpen = false),
                              child: Icon(
                                Icons.close_rounded,
                                color: Colors.white.withOpacity(0.5),
                                size: 20,
                              ),
                            ),
                          ],
                        ),
                      ),
                      Divider(color: Colors.white.withOpacity(0.07)),

                      // New Chat
                      Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 16,
                          vertical: 4,
                        ),
                        child: GestureDetector(
                          onTap: () async {
                            final ts = DateTime.now().millisecondsSinceEpoch;
                            final newSession = 'session_${_activeUserId}_$ts';
                            final r = await http
                                .post(
                                  Uri.parse(
                                    '${DeviceManager.backendUrl}/onboarding/session/create',
                                  ),
                                  headers: {'Content-Type': 'application/json'},
                                  body: jsonEncode({'user_id': _activeUserId}),
                                )
                                .catchError((_) => http.Response('', 500));
                            final sid =
                                r.statusCode == 200
                                    ? (jsonDecode(r.body)['session_id']
                                            as String? ??
                                        newSession)
                                    : newSession;
                            setState(() {
                              _activeSessionId = sid;
                              _responseText = '';
                              _transcribedText = '';
                              _textCtrl.clear();
                              _chatMode = true;
                              _isSidebarOpen = false;
                            });
                            _speechToText.stop();
                            _lastListenStop = DateTime.now();
                            unawaited(_connectThinkingSocket());
                            _loadChats();
                          },
                          child: _sidebarRow(Icons.edit_square, 'New chat'),
                        ),
                      ),

                      const SizedBox(height: 8),

                      Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 20,
                          vertical: 4,
                        ),
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text(
                              'RECENT CHATS',
                              style: _f(
                                AuraTheme.textSecondary,
                                size: 10,
                                weight: FontWeight.w700,
                                spacing: 1.5,
                              ),
                            ),
                            if (_chatsLoading)
                              const SizedBox(
                                width: 12,
                                height: 12,
                                child: CircularProgressIndicator(
                                  strokeWidth: 1.5,
                                  color: AuraTheme.pink400,
                                ),
                              ),
                          ],
                        ),
                      ),

                      // Chat list
                      Expanded(
                        child:
                            _chats.isEmpty && !_chatsLoading
                                ? Padding(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 20,
                                    vertical: 8,
                                  ),
                                  child: Text(
                                    'No chats yet',
                                    style: _f(AuraTheme.textDisabled, size: 12),
                                  ),
                                )
                                : ListView.builder(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 12,
                                  ),
                                  itemCount: _chats.length,
                                  itemBuilder: (ctx, i) {
                                    final chat = _chats[i];
                                    final sid = chat['session_id'] as String;
                                    final title =
                                        chat['title'] as String? ?? 'Chat';
                                    final isActive = sid == _activeSessionId;
                                    return Container(
                                      margin: const EdgeInsets.only(bottom: 2),
                                      decoration: BoxDecoration(
                                        color:
                                            isActive
                                                ? AuraTheme.pink500.withOpacity(
                                                  0.15,
                                                )
                                                : Colors.transparent,
                                        borderRadius: BorderRadius.circular(10),
                                      ),
                                      child: Row(
                                        children: [
                                          Expanded(
                                            child: GestureDetector(
                                              onTap: () {
                                                setState(() {
                                                  _activeSessionId = sid;
                                                  _responseText = '';
                                                  _transcribedText = '';
                                                  _textCtrl.clear();
                                                  _chatMode = true;
                                                  _isSidebarOpen = false;
                                                });
                                                _speechToText.stop();
                                                _lastListenStop =
                                                    DateTime.now();
                                                unawaited(
                                                  _connectThinkingSocket(),
                                                );
                                              },
                                              child: Padding(
                                                padding:
                                                    const EdgeInsets.symmetric(
                                                      horizontal: 12,
                                                      vertical: 10,
                                                    ),
                                                child: Text(
                                                  title,
                                                  style: _f(
                                                    isActive
                                                        ? Colors.white
                                                        : Colors.white
                                                            .withOpacity(0.7),
                                                    size: 13,
                                                    weight:
                                                        isActive
                                                            ? FontWeight.w600
                                                            : FontWeight.w400,
                                                  ),
                                                  maxLines: 1,
                                                  overflow:
                                                      TextOverflow.ellipsis,
                                                ),
                                              ),
                                            ),
                                          ),
                                          GestureDetector(
                                            onTap:
                                                () =>
                                                    _openChatViewer(sid, title),
                                            child: Padding(
                                              padding: const EdgeInsets.all(8),
                                              child: Icon(
                                                Icons.remove_red_eye_outlined,
                                                size: 15,
                                                color: Colors.white.withOpacity(
                                                  0.3,
                                                ),
                                              ),
                                            ),
                                          ),
                                          GestureDetector(
                                            onTap: () => _deleteChat(sid),
                                            child: Padding(
                                              padding: const EdgeInsets.all(8),
                                              child: Icon(
                                                Icons.delete_outline_rounded,
                                                size: 15,
                                                color: Colors.white.withOpacity(
                                                  0.3,
                                                ),
                                              ),
                                            ),
                                          ),
                                        ],
                                      ),
                                    );
                                  },
                                ),
                      ),

                      Divider(color: Colors.white.withOpacity(0.07)),

                      // Sidebar actions
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                        child: Column(
                          children: [
                            // REQ 10: logout button
                            GestureDetector(
                              onTap: () {
                                setState(() => _isSidebarOpen = false);
                                _showLogoutDialog();
                              },
                              child: _sidebarRow(
                                Icons.logout_rounded,
                                'Log out',
                                color: Colors.redAccent,
                              ),
                            ),
                          ],
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
    );
  }

  void _showLogoutDialog() {
    showDialog(
      context: context,
      barrierColor: Colors.black.withOpacity(0.6),
      builder:
          (ctx) => Dialog(
            backgroundColor: Colors.transparent,
            child: ClipRRect(
              borderRadius: BorderRadius.circular(24),
              child: BackdropFilter(
                filter: ImageFilter.blur(sigmaX: 30, sigmaY: 30),
                child: Container(
                  padding: const EdgeInsets.all(28),
                  decoration: BoxDecoration(
                    color: Colors.black.withOpacity(0.65),
                    borderRadius: BorderRadius.circular(24),
                    border: Border.all(color: Colors.white.withOpacity(0.12)),
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        'Log out?',
                        style: _f(
                          Colors.white,
                          size: 18,
                          weight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 10),
                      Text(
                        'You will need to sign in again to use AURA.',
                        style: _f(AuraTheme.textSecondary, size: 13),
                        textAlign: TextAlign.center,
                      ),
                      const SizedBox(height: 24),
                      Row(
                        children: [
                          Expanded(
                            child: GestureDetector(
                              onTap: () => Navigator.pop(ctx),
                              child: Container(
                                padding: const EdgeInsets.symmetric(
                                  vertical: 14,
                                ),
                                decoration: BoxDecoration(
                                  color: Colors.white.withOpacity(0.06),
                                  borderRadius: BorderRadius.circular(14),
                                  border: Border.all(
                                    color: Colors.white.withOpacity(0.12),
                                  ),
                                ),
                                alignment: Alignment.center,
                                child: Text(
                                  'Cancel',
                                  style: _f(
                                    Colors.white,
                                    size: 14,
                                    weight: FontWeight.w600,
                                  ),
                                ),
                              ),
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: GestureDetector(
                              onTap: () {
                                Navigator.pop(ctx);
                                _logout();
                              },
                              child: Container(
                                padding: const EdgeInsets.symmetric(
                                  vertical: 14,
                                ),
                                decoration: BoxDecoration(
                                  color: Colors.redAccent.withOpacity(0.15),
                                  borderRadius: BorderRadius.circular(14),
                                  border: Border.all(
                                    color: Colors.redAccent.withOpacity(0.4),
                                  ),
                                ),
                                alignment: Alignment.center,
                                child: Text(
                                  'Log out',
                                  style: _f(
                                    Colors.redAccent,
                                    size: 14,
                                    weight: FontWeight.w600,
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
    );
  }

  Widget _sidebarRow(IconData icon, String label, {Color? color}) => Container(
    padding: const EdgeInsets.symmetric(vertical: 11, horizontal: 14),
    decoration: BoxDecoration(
      color: AuraTheme.pink900.withOpacity(0.14),
      borderRadius: BorderRadius.circular(12),
      border: Border.all(
        color: (color ?? AuraTheme.pink300).withOpacity(0.55),
        width: 1,
      ),
    ),
    child: Row(
      children: [
        Icon(
          icon,
          color: (color ?? AuraTheme.pink300).withOpacity(0.9),
          size: 16,
        ),
        const SizedBox(width: 12),
        Text(
          label,
          style: _f(
            (color ?? AuraTheme.pink300).withOpacity(0.95),
            size: 13,
            weight: FontWeight.w500,
          ),
        ),
      ],
    ),
  );

  // ── Chat viewer modal ──────────────────────────────────────────────────────

  Widget _buildChatViewerModal() {
    return Stack(
      children: [
        Positioned.fill(
          child: GestureDetector(
            onTap: () => setState(() => _viewingSessionId = null),
            child: Container(color: Colors.black.withOpacity(0.6)),
          ),
        ),
        Center(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(28),
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 40, sigmaY: 40),
              child: Container(
                width: MediaQuery.of(context).size.width * 0.92,
                height: MediaQuery.of(context).size.height * 0.82,
                decoration: BoxDecoration(
                  color: Colors.black.withOpacity(0.6),
                  borderRadius: BorderRadius.circular(28),
                  border: Border.all(
                    color: Colors.white.withOpacity(0.1),
                    width: 1.2,
                  ),
                ),
                child: Column(
                  children: [
                    // Header
                    Padding(
                      padding: const EdgeInsets.fromLTRB(20, 20, 20, 12),
                      child: Row(
                        children: [
                          Container(
                            width: 34,
                            height: 34,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              gradient: const LinearGradient(
                                colors: [AuraTheme.pink500, AuraTheme.pink700],
                              ),
                            ),
                            child: const Center(
                              child: Text(
                                'A',
                                style: TextStyle(
                                  color: Colors.white,
                                  fontSize: 14,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  'AURA',
                                  style: _f(
                                    Colors.white,
                                    size: 14,
                                    weight: FontWeight.w600,
                                  ),
                                ),
                                Text(
                                  _viewingTitle ?? 'Chat',
                                  style: _f(AuraTheme.textSecondary, size: 11),
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ],
                            ),
                          ),
                          GestureDetector(
                            onTap:
                                () => setState(() => _viewingSessionId = null),
                            child: Container(
                              padding: const EdgeInsets.all(8),
                              decoration: BoxDecoration(
                                color: Colors.white.withOpacity(0.06),
                                borderRadius: BorderRadius.circular(10),
                              ),
                              child: const Icon(
                                Icons.close_rounded,
                                color: Colors.white,
                                size: 16,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    Divider(color: Colors.white.withOpacity(0.07), height: 1),
                    // Body — REQ 12: desktop-style bubbles
                    Expanded(
                      child:
                          _viewingLoading
                              ? const Center(
                                child: CircularProgressIndicator(
                                  color: AuraTheme.pink400,
                                ),
                              )
                              : _viewingMessages.isEmpty
                              ? Center(
                                child: Text(
                                  'No messages',
                                  style: _f(AuraTheme.textSecondary, size: 14),
                                ),
                              )
                              : ListView.builder(
                                padding: const EdgeInsets.fromLTRB(
                                  16,
                                  16,
                                  16,
                                  24,
                                ),
                                itemCount: _viewingMessages.length,
                                itemBuilder: (ctx, i) {
                                  final msg = _viewingMessages[i];
                                  final isUser = msg['role'] == 'user';
                                  final content =
                                      msg['content'] as String? ?? '';
                                  return _chatBubble(content, isUser: isUser);
                                },
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

  // ── Settings modal ─────────────────────────────────────────────────────────

  Widget _buildSettingsModal() {
    return Stack(
      children: [
        Positioned.fill(
          child: GestureDetector(
            onTap: () => setState(() => _showSettings = false),
            child: Container(color: Colors.black.withOpacity(0.6)),
          ),
        ),
        Center(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(28),
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 40, sigmaY: 40),
              child: Container(
                width: MediaQuery.of(context).size.width * 0.92,
                height: MediaQuery.of(context).size.height * 0.85,
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                    colors: [
                      const Color(0xFF1B212B).withOpacity(0.62),
                      const Color(0xFF11161D).withOpacity(0.54),
                    ],
                  ),
                  borderRadius: BorderRadius.circular(28),
                  border: Border.all(
                    color: Colors.white.withOpacity(0.12),
                    width: 1.2,
                  ),
                  boxShadow: [
                    BoxShadow(
                      color: Colors.black.withOpacity(0.28),
                      blurRadius: 24,
                      spreadRadius: 0,
                      offset: const Offset(0, 10),
                    ),
                  ],
                ),
                child: Column(
                  children: [
                    // Header
                    Padding(
                      padding: const EdgeInsets.fromLTRB(24, 24, 24, 16),
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            'Settings',
                            style: _f(
                              Colors.white,
                              size: 18,
                              weight: FontWeight.w600,
                            ),
                          ),
                          GestureDetector(
                            onTap: () => setState(() => _showSettings = false),
                            child: Container(
                              padding: const EdgeInsets.all(8),
                              decoration: BoxDecoration(
                                color: Colors.white.withOpacity(0.06),
                                borderRadius: BorderRadius.circular(10),
                              ),
                              child: const Icon(
                                Icons.close_rounded,
                                color: Colors.white,
                                size: 16,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),

                    // Nav tabs
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 20),
                      child: Container(
                        decoration: BoxDecoration(
                          color: Colors.white.withOpacity(0.04),
                          borderRadius: BorderRadius.circular(14),
                          border: Border.all(
                            color: Colors.white.withOpacity(0.07),
                          ),
                        ),
                        child: Row(
                          children: [
                            Expanded(
                              child: _settingsTab(
                                0,
                                'Profile',
                                Icons.person_rounded,
                              ),
                            ),
                            Expanded(
                              child: _settingsTab(
                                1,
                                'Memory',
                                Icons.memory_rounded,
                              ),
                            ),
                            Expanded(
                              child: _settingsTab(
                                2,
                                'Privacy',
                                Icons.security_rounded,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),

                    const SizedBox(height: 16),

                    Expanded(
                      child: AnimatedSwitcher(
                        duration: const Duration(milliseconds: 250),
                        child:
                            _activeSettingsSection == 0
                                ? _buildProfileSettings()
                                : _activeSettingsSection == 1
                                ? _buildMemorySettings()
                                : _buildPrivacySettings(),
                      ),
                    ),

                    // REQ 10: logout at bottom of settings
                    Padding(
                      padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
                      child: GestureDetector(
                        onTap: () {
                          setState(() => _showSettings = false);
                          _showLogoutDialog();
                        },
                        child: Container(
                          width: double.infinity,
                          padding: const EdgeInsets.symmetric(vertical: 14),
                          decoration: BoxDecoration(
                            color: Colors.redAccent.withOpacity(0.08),
                            borderRadius: BorderRadius.circular(16),
                            border: Border.all(
                              color: Colors.redAccent.withOpacity(0.3),
                            ),
                          ),
                          alignment: Alignment.center,
                          child: Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              const Icon(
                                Icons.logout_rounded,
                                color: Colors.redAccent,
                                size: 16,
                              ),
                              const SizedBox(width: 8),
                              Text(
                                'Log out',
                                style: _f(
                                  Colors.redAccent,
                                  size: 14,
                                  weight: FontWeight.w600,
                                ),
                              ),
                            ],
                          ),
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
    );
  }

  Widget _settingsTab(int index, String label, IconData icon) {
    final isActive = _activeSettingsSection == index;
    return GestureDetector(
      onTap: () => setState(() => _activeSettingsSection = index),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.symmetric(vertical: 10),
        decoration: BoxDecoration(
          color: isActive ? Colors.white.withOpacity(0.1) : Colors.transparent,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              icon,
              size: 14,
              color: isActive ? Colors.white : AuraTheme.textSecondary,
            ),
            const SizedBox(width: 6),
            Text(
              label,
              style: _f(
                isActive ? Colors.white : AuraTheme.textSecondary,
                size: 12,
                weight: isActive ? FontWeight.w600 : FontWeight.w500,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildProfileSettings() {
    return SingleChildScrollView(
      key: const ValueKey('profile'),
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
      physics: const BouncingScrollPhysics(),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _settingsLabel('Username'),
          const SizedBox(height: 8),
          _settingsTextField(_usernameSettingsCtrl, 'Username'),
          const SizedBox(height: 16),
          _settingsLabel('Email'),
          const SizedBox(height: 8),
          _settingsTextField(
            _emailSettingsCtrl,
            'Email',
            type: TextInputType.emailAddress,
          ),
          const SizedBox(height: 24),
          if (_profileSaveStatus != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Text(
                _profileSaveStatus!,
                style: _f(
                  _profileSaveStatus!.startsWith('✓')
                      ? AuraTheme.success
                      : AuraTheme.error,
                  size: 13,
                ),
              ),
            ),
          GestureDetector(
            onTap: _profileSaving ? () {} : _saveProfile,
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(vertical: 14),
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: AuraTheme.pink900.withOpacity(0.12),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: AuraTheme.pink300.withOpacity(0.72)),
              ),
              child: Text(
                _profileSaving ? 'Saving...' : 'Save Changes',
                style: _f(AuraTheme.pink300, size: 14, weight: FontWeight.w700),
              ),
            ),
          ),
          const SizedBox(height: 20),
        ],
      ),
    );
  }

  Widget _buildMemorySettings() {
    return SingleChildScrollView(
      key: const ValueKey('memory'),
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
      physics: const BouncingScrollPhysics(),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: _memoryCard('Preferences', '24', AuraTheme.pink400),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: _memoryCard('Personal Info', '8', AuraTheme.pink300),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: _memoryCard('App Settings', '16', AuraTheme.pink200),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: _memoryCard(
                  'Storage',
                  '1.2 MB',
                  AuraTheme.textSecondary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 24),
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: Colors.white.withOpacity(0.03),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: Colors.white.withOpacity(0.06)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Manage Memory',
                  style: _f(Colors.white, size: 14, weight: FontWeight.w600),
                ),
                const SizedBox(height: 8),
                Text(
                  'Clear all stored preferences and context.',
                  style: _f(AuraTheme.textSecondary, size: 12, height: 1.4),
                ),
                const SizedBox(height: 16),
                _actionBtn(
                  'Clear Memory',
                  Colors.redAccent.withOpacity(0.8),
                  () async {
                    await http
                        .delete(
                          Uri.parse(
                            '${DeviceManager.backendUrl}/api/memory/clear-preferences?user_id=$_activeUserId',
                          ),
                        )
                        .catchError((_) => http.Response('', 500));
                  },
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),
        ],
      ),
    );
  }

  // REQ 4: privacy settings with accessibility toggle
  Widget _buildPrivacySettings() {
    return SingleChildScrollView(
      key: const ValueKey('privacy'),
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
      physics: const BouncingScrollPhysics(),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Permissions',
            style: _f(
              AuraTheme.textSecondary,
              size: 11,
              weight: FontWeight.w700,
              spacing: 1.5,
            ),
          ),
          const SizedBox(height: 12),
          _toggleTile(
            icon: Icons.accessibility_new_rounded,
            title: 'Accessibility Service',
            subtitle: 'Allows AURA to interact with other apps on your screen.',
            value: _accessibilityEnabled,
            onChanged: (val) => _saveAccessibilityPref(val),
            accent: const Color(0xFF7FA5C9),
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: Colors.white.withOpacity(0.04),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: Colors.white.withOpacity(0.1)),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  Icons.info_outline_rounded,
                  color: AuraTheme.textSecondary,
                  size: 16,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    'Accessibility permission is required for AURA to automate tasks. It is only used when you explicitly ask AURA to perform actions.',
                    style: _f(AuraTheme.textSecondary, size: 12, height: 1.5),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 20),
          if (!_serviceEnabled && _accessibilityEnabled)
            Column(
              children: [
                _actionBtn(
                  'Open Accessibility Settings',
                  const Color(0xFF4A5A70),
                  _openAccessibilitySettings,
                ),
                const SizedBox(height: 8),
                _actionBtn(
                  'Refresh Status',
                  Colors.white.withOpacity(0.1),
                  _checkServiceStatus,
                ),
              ],
            ),
          const SizedBox(height: 20),
        ],
      ),
    );
  }

  Widget _toggleTile({
    required IconData icon,
    required String title,
    required String subtitle,
    required bool value,
    required ValueChanged<bool> onChanged,
    Color accent = Colors.white,
  }) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.03),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color:
              value ? accent.withOpacity(0.25) : Colors.white.withOpacity(0.07),
        ),
      ),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color:
                  value
                      ? accent.withOpacity(0.12)
                      : Colors.white.withOpacity(0.05),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Icon(
              icon,
              color: value ? accent : AuraTheme.textSecondary,
              size: 18,
            ),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: _f(Colors.white, size: 13, weight: FontWeight.w600),
                ),
                const SizedBox(height: 2),
                Text(
                  subtitle,
                  style: _f(AuraTheme.textSecondary, size: 11, height: 1.4),
                ),
              ],
            ),
          ),
          Switch(
            value: value,
            onChanged: onChanged,
            activeColor: accent,
            activeTrackColor: accent.withOpacity(0.3),
            inactiveThumbColor: Colors.white.withOpacity(0.4),
            inactiveTrackColor: Colors.white.withOpacity(0.1),
          ),
        ],
      ),
    );
  }

  Widget _settingsLabel(String label) => Text(
    label,
    style: _f(
      AuraTheme.textSecondary,
      size: 12,
      weight: FontWeight.w600,
      spacing: 0.5,
    ),
  );

  Widget _settingsTextField(
    TextEditingController ctrl,
    String hint, {
    TextInputType? type,
  }) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.04),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: Colors.white.withOpacity(0.1)),
      ),
      child: TextField(
        controller: ctrl,
        style: _f(Colors.white, size: 14),
        keyboardType: type,
        decoration: InputDecoration(
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 16,
            vertical: 14,
          ),
          border: InputBorder.none,
          hintText: hint,
          hintStyle: _f(AuraTheme.textDisabled, size: 14),
        ),
      ),
    );
  }

  Widget _memoryCard(String label, String value, Color color) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.03),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withOpacity(0.06)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(value, style: _f(color, size: 22, weight: FontWeight.w700)),
          const SizedBox(height: 4),
          Text(
            label,
            style: _f(
              AuraTheme.textSecondary,
              size: 11,
              weight: FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }

  Widget _actionBtn(String label, Color color, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(vertical: 14),
        alignment: Alignment.center,
        decoration: BoxDecoration(
          gradient: LinearGradient(
            colors: [color.withOpacity(0.85), color.withOpacity(0.6)],
          ),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: Colors.white.withOpacity(0.2)),
          boxShadow: [
            BoxShadow(
              color: color.withOpacity(0.25),
              blurRadius: 12,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: Text(
          label,
          style: _f(Colors.white, size: 14, weight: FontWeight.w600),
        ),
      ),
    );
  }
}

class TypewriterText extends StatelessWidget {
  final String text;
  final TextStyle style;
  final Duration duration;
  final int maxLines;
  final TextAlign textAlign;

  const TypewriterText({
    super.key,
    required this.text,
    required this.style,
    this.duration = const Duration(milliseconds: 700),
    this.maxLines = 8,
    this.textAlign = TextAlign.left,
  });

  @override
  Widget build(BuildContext context) {
    final source = text.trim().isEmpty ? ' ' : text;
    return TweenAnimationBuilder<double>(
      key: ValueKey(source),
      tween: Tween(begin: 0, end: 1),
      duration: duration,
      curve: Curves.easeOutCubic,
      builder: (context, value, _) {
        final length = (source.length * value).clamp(0, source.length).round();
        final display = source.substring(0, length);
        return Text(
          display.isEmpty ? ' ' : display,
          style: style,
          maxLines: maxLines,
          textAlign: textAlign,
          overflow: TextOverflow.ellipsis,
        );
      },
    );
  }
}

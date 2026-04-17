import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:ui';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import 'package:video_player/video_player.dart';

import 'screens/startup_screen.dart';
import 'theme.dart';
import 'widgets/execution_widget.dart';
import 'widgets/task_execution_border.dart';
import 'widgets/voice_spectrum_visualizer.dart';

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

class MyApp extends StatelessWidget {
  const MyApp({super.key});
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'AURA',
      theme: AuraTheme.darkTheme,
      home: const StartupScreen(),
    );
  }
}

// ─── Device Manager ───────────────────────────────────────────────────────────
class DeviceManager {
  static const String backendUrl = 'http://10.0.2.2:8000';
  static const String deviceId = 'android_device_1';
  static const _platform = MethodChannel('com.example.automation/service');

  static Future<bool> registerDevice() async {
    try {
      final r = await http.post(
        Uri.parse('$backendUrl/device/$deviceId/register'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'name': 'Flutter Device',
          'android_version': '14',
          'device_model': 'Emulator',
          'screen_width': 1080,
          'screen_height': 2340,
        }),
      );
      return r.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  static Future<bool> sendUITree(Map<String, dynamic> tree) async {
    try {
      final r = await http.post(
        Uri.parse('$backendUrl/device/$deviceId/ui-tree'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode(tree),
      );
      return r.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  static Future<bool> sendStatus() async {
    try {
      final r = await http.post(
        Uri.parse('$backendUrl/device/$deviceId/status'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'status': 'online',
          'android_version': '14',
          'screen_width': 1080,
          'screen_height': 2340,
        }),
      );
      return r.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  static Future<Map<String, dynamic>> getAccessibilityTree() async {
    try {
      final result = await _platform.invokeMethod('getAccessibilityTree');
      if (result is String) return jsonDecode(result) as Map<String, dynamic>;
      if (result is Map) return Map<String, dynamic>.from(result);
      return {};
    } catch (_) {
      return {};
    }
  }

  static Future<Map<String, dynamic>> executeAction(
    Map<String, dynamic> action,
  ) async {
    try {
      final type = action['action_type'] ?? '';
      switch (type) {
        case 'click':
          await _platform.invokeMethod('executeAction', {
            'action_type': 'click',
            'element_id': action['element_id'],
          });
          await Future.delayed(const Duration(milliseconds: 1500));
          final t = await getAccessibilityTree();
          if (t.isNotEmpty) await sendUITree(t);
          break;
        case 'type':
          await _platform.invokeMethod('executeAction', {
            'action_type': 'type',
            'element_id': action['element_id'],
            'text': action['text'],
          });
          await Future.delayed(const Duration(milliseconds: 500));
          final t = await getAccessibilityTree();
          if (t.isNotEmpty) await sendUITree(t);
          break;
        case 'scroll':
          await _platform.invokeMethod('executeAction', {
            'action_type': 'scroll',
            'direction': action['direction'] ?? 'down',
          });
          await Future.delayed(const Duration(milliseconds: 800));
          final t = await getAccessibilityTree();
          if (t.isNotEmpty) await sendUITree(t);
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
          final t = await getAccessibilityTree();
          if (t.isNotEmpty) await sendUITree(t);
          break;
        case 'navigate_back':
          await _platform.invokeMethod('executeAction', {
            'action_type': 'global_action',
            'action_name': 'BACK',
          });
          await Future.delayed(const Duration(milliseconds: 1000));
          final t = await getAccessibilityTree();
          if (t.isNotEmpty) await sendUITree(t);
          break;
        case 'global_action':
          final name =
              action['global_action'] ?? action['action_name'] ?? 'HOME';
          await _platform.invokeMethod('executeAction', {
            'action_type': 'global_action',
            'action_name': name,
          });
          await Future.delayed(const Duration(milliseconds: 1000));
          final t = await getAccessibilityTree();
          if (t.isNotEmpty) await sendUITree(t);
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
  late String _userName;

  // REQ 4: accessibility toggle in settings
  bool _accessibilityEnabled = false;

  HttpServer? _actionServer;
  late AnimationController _pulseCtrl;
  late AnimationController _thinkCtrl;
  late AnimationController _waveCtrl; // REQ 6: wave visualizer
  late VideoPlayerController _videoCtrl;
  final AudioPlayer _audioPlayer = AudioPlayer();

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

    // REQ 3: aura_calm.webm for main pages
    _videoCtrl = VideoPlayerController.asset('assets/aura_calm.webm')
      ..initialize().then((_) {
        setState(() {});
        _videoCtrl
          ..setLooping(true)
          ..play();
      });
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
    if (await DeviceManager.registerDevice()) {
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
      setState(() => _status = 'Error: $e');
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
      setState(() => _status = 'Error: $e');
    }
  }

  Future<void> _sendTextToBackend(String text) async {
    if (text.trim().isEmpty) return;
    final hadPendingFollowup = _needsConfirmation;
    setState(() {
      _isLoading = true;
      _isExecuting = true; // REQ 14
      _isThinking = true;
      _status = 'Processing...';
      _responseText = '';
      _showExecutionWidget = true;
      _executionWidgetMinimized = true;
      _executionNeedsAttention = false;
      _executionWidgetTitle = 'Executing task';
      _executionWidgetSubtitle =
          hadPendingFollowup
              ? 'Applying your clarification...'
              : 'AURA is running in background';
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
      setState(() {
        _isLoading = false;
        _isExecuting = false; // REQ 14
        _isThinking = false;
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
            _showExecutionWidget = true;
            _executionWidgetMinimized = false;
            _executionNeedsAttention = true;
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
            _showExecutionWidget = true;
            _executionWidgetMinimized = false;
            _executionNeedsAttention = true;
            _executionWidgetTitle = 'Execution complete';
            _executionWidgetSubtitle = _compactForWidget(_responseText);
          }
          _textCtrl.clear();
          _transcribedText = '';
          _playTTSAudio(_responseText);
        } else {
          _status = 'Error';
          _responseText = data['error']?.toString() ?? 'Unknown error';
          _showExecutionWidget = true;
          _executionWidgetMinimized = false;
          _executionNeedsAttention = true;
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
        _responseText = e.toString();
        _confirmationRequest = '';
        _draftedMessage = '';
        _needsConfirmation = false;
        _showExecutionWidget = true;
        _executionWidgetMinimized = false;
        _executionNeedsAttention = true;
        _executionWidgetTitle = 'Execution failed';
        _executionWidgetSubtitle = _compactForWidget(_responseText);
      });
    }
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
    try {
      final r = await http.post(
        Uri.parse('${DeviceManager.backendUrl}/text-to-speech'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'text': text}),
      );
      if (r.statusCode == 200) {
        final data = jsonDecode(r.body) as Map<String, dynamic>;
        final bytes = base64.decode(data['audio_data'] as String);
        final fmt = data['format'] ?? 'mp3';
        final tmp = File('${Directory.systemTemp.path}/tts.$fmt');
        await tmp.writeAsBytes(bytes);
        await _audioPlayer.play(DeviceFileSource(tmp.path));
        _audioPlayer.onPlayerComplete.listen((_) => tmp.delete());
      }
    } catch (_) {}
  }

  Future<void> _toggleRecording() async {
    try {
      if (_isRecording) {
        _pulseCtrl.stop();
        _pulseCtrl.reset();
        setState(() => _status = 'Processing audio...');
        final result = await _platform.invokeMethod('toggleRecording');
        if (result is Map && result['status'] == 'success') {
          final transcript = result['transcript'] ?? '';
          setState(() {
            _isRecording = false;
            _transcribedText = transcript;
            _status = 'Transcribed: $transcript';
          });
          if (transcript.isNotEmpty) await _sendTextToBackend(transcript);
        }
      } else {
        _pulseCtrl.repeat(reverse: true);
        final result = await _platform.invokeMethod('toggleRecording');
        if (result is Map && result['status'] == 'recording') {
          setState(() {
            _isRecording = true;
            _status = 'Recording...';
            _transcribedText = '';
          });
        }
      }
    } catch (e) {
      setState(() {
        _isRecording = false;
        _status = 'Mic error: $e';
      });
      _pulseCtrl.stop();
      _pulseCtrl.reset();
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

  @override
  void dispose() {
    _textCtrl.dispose();
    _usernameSettingsCtrl.dispose();
    _emailSettingsCtrl.dispose();
    _actionServer?.close();
    _pulseCtrl.dispose();
    _thinkCtrl.dispose();
    _waveCtrl.dispose();
    _videoCtrl.dispose();
    _audioPlayer.dispose();
    super.dispose();
  }

  // ── Build ──────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return TaskExecutionBorder(
      isExecuting: _isLoading || _isRecording,
      child: Scaffold(
        backgroundColor: Colors.black,
        body: Stack(
          children: [
            // REQ 3: aura_calm.webm background for main pages
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

            SafeArea(child: _chatMode ? _buildChatMode() : _buildVoiceMode()),
            if (!_isSidebarOpen) _buildPinnedSidebarToggle(),
            _buildSidebar(),
            if (_showSettings) _buildSettingsModal(),
            if (_viewingSessionId != null) _buildChatViewerModal(),
            if (!_showSettings && _viewingSessionId == null)
              ExecutionWidget(
                visible: _showExecutionWidget,
                minimized: _executionWidgetMinimized,
                isExecuting: _isExecuting,
                isPaused: _isPaused,
                needsAttention: _executionNeedsAttention,
                title: _executionWidgetTitle,
                subtitle: _executionWidgetSubtitle,
                animation: _waveCtrl,
                onToggleMinimize:
                    () => setState(
                      () =>
                          _executionWidgetMinimized =
                              !_executionWidgetMinimized,
                    ),
                onPauseResume:
                    () => _isPaused ? _resumeExecution() : _pauseExecution(),
                onStop: _stopExecution,
              ),
          ],
        ),
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
        // REQ 13: HeaderContent-style header
        _buildHeader(),

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
                  color: AuraTheme.bgSurface.withOpacity(0.4),
                  padding: const EdgeInsets.all(20),
                  child: ListView(
                    physics: const BouncingScrollPhysics(),
                    children: [
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

  // REQ 13: contextual header like desktop
  Widget _buildHeader() {
    final hour = DateTime.now().hour;
    final emoji =
        hour < 12
            ? '🌅'
            : hour < 18
            ? '☀️'
            : '🌙';
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
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
                  '${_greeting()} $emoji',
                  style: _f(AuraTheme.textSecondary, size: 13),
                ),
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

  Widget _buildPinnedSidebarToggle() {
    return Positioned(
      left: 12,
      top: 120,
      child: GestureDetector(
        onTap: () => setState(() => _isSidebarOpen = true),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(16),
          child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 16, sigmaY: 16),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 260),
              curve: Curves.easeOutCubic,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 12),
              decoration: BoxDecoration(
                color: Colors.black.withOpacity(0.28),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: Colors.white.withOpacity(0.22)),
                boxShadow: [
                  BoxShadow(
                    color: AuraTheme.pink500.withOpacity(0.16),
                    blurRadius: 14,
                    offset: const Offset(0, 5),
                  ),
                ],
              ),
              child: const Icon(
                Icons.menu_rounded,
                color: Colors.white,
                size: 20,
              ),
            ),
          ),
        ),
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
    return ClipRRect(
      borderRadius: BorderRadius.circular(30),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 15, sigmaY: 15),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          decoration: BoxDecoration(
            color: Colors.black.withOpacity(0.45),
            borderRadius: BorderRadius.circular(30),
            border: Border.all(color: Colors.white.withOpacity(0.1), width: 1),
          ),
          child: Row(
            children: [
              const SizedBox(width: 8),
              Expanded(
                child: TextField(
                  controller: _textCtrl,
                  style: _f(AuraTheme.textPrimary, size: 15),
                  decoration: InputDecoration(
                    hintText: 'Ask anything...',
                    hintStyle: _f(AuraTheme.textMuted, size: 15),
                    border: InputBorder.none,
                    isDense: true,
                  ),
                  onSubmitted: (val) {
                    if (val.isNotEmpty) {
                      _sendTextToBackend(val);
                      _textCtrl.clear();
                    }
                  },
                ),
              ),
              const SizedBox(width: 8),
              GestureDetector(
                onTap: () {
                  if (_textCtrl.text.isNotEmpty) {
                    _sendTextToBackend(_textCtrl.text);
                    _textCtrl.clear();
                  }
                },
                child: Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: const LinearGradient(
                      colors: [AuraTheme.pink500, AuraTheme.pink700],
                    ),
                    boxShadow: [
                      BoxShadow(
                        color: AuraTheme.pink500.withOpacity(0.4),
                        blurRadius: 12,
                        offset: const Offset(0, 3),
                      ),
                    ],
                  ),
                  child: const Icon(
                    Icons.arrow_upward_rounded,
                    color: Colors.white,
                    size: 18,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              GestureDetector(
                onTap:
                    () => setState(() {
                      _chatMode = false;
                      _toggleRecording();
                    }),
                child: Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: AuraTheme.pink400.withOpacity(0.15),
                    border: Border.all(
                      color: AuraTheme.pink400.withOpacity(0.3),
                    ),
                  ),
                  child: const Icon(
                    Icons.mic_rounded,
                    color: AuraTheme.pink400,
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

  Widget _buildVoiceMode() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 20),
      child: Column(
        children: [
          // REQ 13: header on voice mode too
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              GestureDetector(
                onTap: () => setState(() => _isSidebarOpen = true),
                child: _iconBtn(Icons.menu_rounded),
              ),
              Column(
                children: [
                  Text(_greeting(), style: _f(AuraTheme.textMuted, size: 11)),
                  Text(
                    'AURA',
                    style: _f(
                      AuraTheme.textPrimary,
                      size: 15,
                      weight: FontWeight.w700,
                      spacing: 2,
                    ),
                  ),
                ],
              ),
              GestureDetector(
                onTap: () => setState(() => _showSettings = true),
                child: _iconBtn(Icons.settings_rounded),
              ),
            ],
          ),

          Expanded(
            flex: 2,
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                AnimatedBuilder(
                  animation: _pulseCtrl,
                  builder: (context, child) {
                    return Transform.scale(
                      scale: 0.9 + (_pulseCtrl.value * 0.1),
                      child: Image.asset(
                        'assets/aura_icon_haze.png',
                        width: 72,
                        height: 72,
                        fit: BoxFit.contain,
                      ),
                    );
                  },
                ),
              ],
            ),
          ),

          const SizedBox(height: 8),

          _buildVoiceGlassContainer(
            borderRadius: 28,
            sigma: 18,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            child: SizedBox(height: 170, child: _buildMiddleContextPanel()),
          ),

          const SizedBox(height: 10),

          // Controls pill
          _buildVoiceGlassContainer(
            borderRadius: 50,
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                GestureDetector(
                  onTap:
                      () => setState(() {
                        _chatMode = true;
                        if (_isRecording) _toggleRecording();
                      }),
                  child: Container(
                    padding: const EdgeInsets.all(10),
                    color: Colors.transparent,
                    child: const Icon(
                      Icons.close_rounded,
                      color: AuraTheme.textSecondary,
                      size: 22,
                    ),
                  ),
                ),
                AnimatedBuilder(
                  animation: _pulseCtrl,
                  builder: (context, child) {
                    String status = 'Ready';
                    if (_isRecording) status = 'Listening...';
                    if (_isThinking) status = 'Processing...';
                    return Text(
                      status,
                      style: _f(
                        _isRecording
                            ? AuraTheme.pink400
                            : AuraTheme.textSecondary,
                        size: 15,
                        weight: FontWeight.w500,
                      ),
                    );
                  },
                ),
                GestureDetector(
                  onTap: () => setState(() => _showSettings = true),
                  child: Container(
                    padding: const EdgeInsets.all(10),
                    color: Colors.transparent,
                    child: const Icon(
                      Icons.settings_rounded,
                      color: AuraTheme.textSecondary,
                      size: 22,
                    ),
                  ),
                ),
              ],
            ),
          ),

          const SizedBox(height: 12),

          Expanded(
            flex: 4,
            child: _buildVoiceGlassContainer(
              borderRadius: 32,
              child: Column(
                children: [
                  Expanded(
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10),
                      alignment: Alignment.center,
                      child: SingleChildScrollView(
                        physics: const BouncingScrollPhysics(),
                        child: Text(
                          _transcribedText.isEmpty && _isRecording
                              ? 'Speak now...'
                              : (_transcribedText.isEmpty && !_isRecording
                                  ? 'Ready'
                                  : _transcribedText),
                          style: _f(
                            AuraTheme.textPrimary,
                            size: 24,
                            weight: FontWeight.w300,
                            height: 1.4,
                          ),
                          textAlign: TextAlign.center,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),

                  // REQ 6: aesthetic wave visualizer
                  _buildWaveVisualizer(),
                  const SizedBox(height: 20),

                  // Mic button
                  GestureDetector(
                    onTap: _toggleRecording,
                    child: AnimatedBuilder(
                      animation: _pulseCtrl,
                      builder: (context, child) {
                        return Container(
                          padding: const EdgeInsets.all(18),
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            gradient:
                                _isRecording
                                    ? const LinearGradient(
                                      colors: [
                                        AuraTheme.pink400,
                                        AuraTheme.pink700,
                                      ],
                                      begin: Alignment.topLeft,
                                      end: Alignment.bottomRight,
                                    )
                                    : null,
                            color: _isRecording ? null : AuraTheme.bgMuted,
                            boxShadow: [
                              BoxShadow(
                                color: (_isRecording
                                        ? AuraTheme.pink500
                                        : Colors.transparent)
                                    .withOpacity(0.4 * _pulseCtrl.value),
                                blurRadius: 24,
                                spreadRadius: 12,
                              ),
                            ],
                          ),
                          child: Icon(
                            _isRecording
                                ? Icons.stop_rounded
                                : Icons.mic_rounded,
                            color: AuraTheme.textPrimary,
                            size: 30,
                          ),
                        );
                      },
                    ),
                  ),
                  const SizedBox(height: 12),
                ],
              ),
            ),
          ),
        ],
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

  // REQ 6: VoiceControls.jsx-style wave visualizer
  Widget _buildWaveVisualizer() {
    return VoiceSpectrumVisualizer(
      animation: _waveCtrl,
      active: _isRecording || _isThinking,
      bars: 22,
      height: 52,
      color: AuraTheme.pink400,
    );
  }

  Widget _buildVoiceGlassContainer({
    required Widget child,
    double borderRadius = 24,
    double sigma = 20,
    EdgeInsetsGeometry padding = const EdgeInsets.all(20),
  }) {
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        border: Border.all(color: Colors.white.withOpacity(0.08), width: 1),
        borderRadius: BorderRadius.circular(borderRadius),
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(borderRadius),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: sigma, sigmaY: sigma),
          child: Container(
            padding: padding,
            color: AuraTheme.bgSurface.withOpacity(0.4),
            child: child,
          ),
        ),
      ),
    );
  }

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

                      // Settings + Logout
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                        child: Column(
                          children: [
                            GestureDetector(
                              onTap:
                                  () => setState(() {
                                    _showSettings = true;
                                    _isSidebarOpen = false;
                                  }),
                              child: _sidebarRow(
                                Icons.settings_rounded,
                                'Settings',
                              ),
                            ),
                            const SizedBox(height: 6),
                            // REQ 10: logout button
                            GestureDetector(
                              onTap: () {
                                setState(() => _isSidebarOpen = false);
                                _showLogoutDialog();
                              },
                              child: _sidebarRow(
                                Icons.logout_rounded,
                                'Log out',
                                color: Colors.redAccent.withOpacity(0.8),
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
      color: Colors.white.withOpacity(0.03),
      borderRadius: BorderRadius.circular(12),
    ),
    child: Row(
      children: [
        Icon(icon, color: (color ?? Colors.white).withOpacity(0.8), size: 16),
        const SizedBox(width: 12),
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
                      Colors.white.withOpacity(0.08),
                      Colors.white.withOpacity(0.02),
                    ],
                  ),
                  borderRadius: BorderRadius.circular(28),
                  border: Border.all(
                    color: Colors.white.withOpacity(0.2),
                    width: 1.2,
                  ),
                  boxShadow: [
                    BoxShadow(
                      color: AuraTheme.pink500.withOpacity(0.15),
                      blurRadius: 26,
                      spreadRadius: 2,
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
          _actionBtn(
            _profileSaving ? 'Saving...' : 'Save Changes',
            AuraTheme.pink500,
            _profileSaving ? () {} : _saveProfile,
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
            accent: AuraTheme.pink400,
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: AuraTheme.pink900.withOpacity(0.15),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: AuraTheme.pink400.withOpacity(0.2)),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  Icons.info_outline_rounded,
                  color: AuraTheme.pink300,
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
                  AuraTheme.pink500,
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

import 'dart:convert';
import 'dart:io';
import 'dart:ui';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:video_player/video_player.dart';

import 'screens/startup_screen.dart';
import 'theme.dart';
import 'widgets/task_execution_border.dart';

// Convenience helper — plain TextStyle, no google_fonts network call
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

// ─── App colours ──────────────────────────────────────────────────────────────
class AppColors {
  static const darkPlum4 = Color(0xFF1B0E25);
}

// ─── HomeWrapper / AutomationDemo ────────────────────────────────────────────
class HomeWrapper extends StatelessWidget {
  const HomeWrapper({super.key});
  @override
  Widget build(BuildContext context) => const AutomationDemo();
  // AutomationDemo defaults keep working for the existing OnboardingScreen flow
}

class AutomationDemo extends StatefulWidget {
  final String userId;
  final String username;
  final String sessionId;
  final String language;

  const AutomationDemo({
    super.key,
    this.userId = 'flutter_user',   // fallback for HomeWrapper direct usage
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

  // ignore: unused_field
  late String _activeUserId;
  late String _activeSessionId;
  String _status = 'Waiting...';
  bool _serviceEnabled = false;
  bool _isLoading = false;
  bool _isRecording = false;
  bool _isSidebarOpen = false;
  bool _chatMode = false;
  bool _showSettings = false;
  int _activeSettingsSection = 0; // 0: Profile, 1: Memory

  final TextEditingController _textCtrl = TextEditingController();
  String _responseText = '';
  String _transcribedText = '';
  bool _isThinking = false;
  // final String _userName = 'User';
  late String _userName;

  HttpServer? _actionServer;
  late AnimationController _pulseCtrl;
  late AnimationController _thinkCtrl;
  late VideoPlayerController _videoCtrl;
  final AudioPlayer _audioPlayer = AudioPlayer();

@override
  void initState() {
    super.initState();
    _activeUserId = widget.userId;
    _activeSessionId = widget.sessionId.isEmpty
        ? 'flutter_${DateTime.now().millisecondsSinceEpoch}'
        : widget.sessionId;
    _userName = widget.username.isNotEmpty ? widget.username : 'User';
    _setupMethodChannelListener();
    _checkServiceStatus();
    _registerWithBackend();
    _startActionServer();
    _startPollingForActions();

    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    );
    _thinkCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1000),
    );

    _videoCtrl = VideoPlayerController.asset('assets/aura1.webm')
      ..initialize().then((_) {
        setState(() {});
        _videoCtrl
          ..setLooping(true)
          ..play();
      });
  }

  // ── helpers ────────────────────────────────────────────────────────────────

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
    setState(() {
      _isLoading = true;
      _isThinking = true;
      _status = 'Processing...';
      _responseText = '';
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
        _isThinking = false;
        _thinkCtrl.stop();
        _thinkCtrl.reset();
        if (resp.statusCode == 200) {
          if (data['status'] == 'clarification_needed') {
            _status = 'Question:';
            _responseText = data['question'] ?? 'Clarification needed';
          } else {
            _status = 'Done';
            _responseText =
                data['text'] ?? data['response'] ?? 'Task completed';
          }
          _textCtrl.clear();
          _transcribedText = '';
          _playTTSAudio(_responseText);
        } else {
          _status = 'Error';
          _responseText = data['error']?.toString() ?? 'Unknown error';
        }
      });
    } catch (e) {
      setState(() {
        _isLoading = false;
        _isThinking = false;
        _thinkCtrl.stop();
        _thinkCtrl.reset();
        _status = 'Error';
        _responseText = e.toString();
      });
    }
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

  String _greeting() {
    final h = DateTime.now().hour;
    if (h >= 5 && h < 9) return 'Rise and shine, $_userName!';
    if (h >= 9 && h < 12) return 'Good morning, $_userName!';
    if (h >= 12 && h < 15) return 'Lunchtime, $_userName?';
    if (h >= 15 && h < 18) return 'Good afternoon, $_userName!';
    if (h >= 18 && h < 21) return 'Evening, $_userName!';
    return 'Hello night owl, $_userName!';
  }

  @override
  void dispose() {
    _textCtrl.dispose();
    _actionServer?.close();
    _pulseCtrl.dispose();
    _thinkCtrl.dispose();
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
        backgroundColor:
            Colors
                .black, // AuraTheme.bgBase usually, but black is good behind video
        body: Stack(
          children: [
            // Video Background
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

            _buildSidebar(),
            if (_showSettings) _buildSettingsModal(),
          ],
        ),
      ),
    );
  }

  Widget _buildChatMode() {
    return Column(
      children: [
        // Header
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 15),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              GestureDetector(
                onTap: () => setState(() => _isSidebarOpen = true),
                child: Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: AuraTheme.bgElevated.withOpacity(0.5),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: Colors.white.withOpacity(0.05)),
                  ),
                  child: const Icon(
                    Icons.menu_rounded,
                    color: AuraTheme.textSecondary,
                    size: 22,
                  ),
                ),
              ),
              Text(
                'AURA',
                style: _f(
                  AuraTheme.textPrimary,
                  size: 16,
                  weight: FontWeight.w600,
                  spacing: 1.5,
                ),
              ),
              GestureDetector(
                onTap: () {
                  // close/minimize logic
                },
                child: Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: AuraTheme.bgElevated.withOpacity(0.5),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: Colors.white.withOpacity(0.05)),
                  ),
                  child: const Icon(
                    Icons.close_rounded,
                    color: AuraTheme.textSecondary,
                    size: 22,
                  ),
                ),
              ),
            ],
          ),
        ),

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
                        const SizedBox(height: 40),
                        Text(
                          _greeting(),
                          style: _f(AuraTheme.textMuted, size: 14),
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 12),
                        Text(
                          'What would you like to know?',
                          style: _f(
                            AuraTheme.textPrimary,
                            size: 24,
                            weight: FontWeight.w600,
                          ),
                          textAlign: TextAlign.center,
                        ),
                      ],

                      // User message
                      if (_transcribedText.isNotEmpty) ...[
                        Align(
                          alignment: Alignment.centerRight,
                          child: AnimatedOpacity(
                            opacity: 1.0,
                            duration: const Duration(milliseconds: 500),
                            child: Text(
                              _transcribedText,
                              style: _f(
                                AuraTheme.textSecondary,
                                size: 16,
                                weight: FontWeight.w400,
                              ),
                              textAlign: TextAlign.right,
                            ),
                          ),
                        ),
                        const SizedBox(height: 30),
                      ],

                      // Thinking indicator
                      if (_isThinking) ...[
                        Align(
                          alignment: Alignment.centerLeft,
                          child: AnimatedBuilder(
                            animation: _thinkCtrl,
                            builder: (_, __) {
                              return Opacity(
                                opacity: 0.5 + (_thinkCtrl.value * 0.5),
                                child: Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    const Icon(
                                      Icons.auto_awesome,
                                      color: AuraTheme.pink400,
                                      size: 16,
                                    ),
                                    const SizedBox(width: 8),
                                    Text(
                                      'Thinking...',
                                      style: _f(
                                        AuraTheme.pink300,
                                        size: 15,
                                        weight: FontWeight.w500,
                                      ),
                                    ),
                                  ],
                                ),
                              );
                            },
                          ),
                        ),
                        const SizedBox(height: 30),
                      ],

                      // AI Response
                      if (_responseText.isNotEmpty && !_isThinking) ...[
                        Align(
                          alignment: Alignment.centerLeft,
                          child: AnimatedOpacity(
                            opacity: 1.0,
                            duration: const Duration(milliseconds: 500),
                            child: Text(
                              _responseText,
                              style: _f(
                                AuraTheme.textPrimary,
                                size: 16,
                                weight: FontWeight.w500,
                                height: 1.6,
                              ),
                            ),
                          ),
                        ),
                        const SizedBox(height: 30),
                      ],

                      if (!_serviceEnabled)
                        Padding(
                          padding: const EdgeInsets.only(top: 20, bottom: 20),
                          child: Container(
                            padding: const EdgeInsets.all(16),
                            decoration: BoxDecoration(
                              color: AuraTheme.pink900.withOpacity(0.2),
                              borderRadius: BorderRadius.circular(16),
                              border: Border.all(
                                color: AuraTheme.pink400.withOpacity(0.3),
                              ),
                            ),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(
                                  children: [
                                    const Icon(
                                      Icons.warning_amber_rounded,
                                      color: AuraTheme.pink400,
                                      size: 20,
                                    ),
                                    const SizedBox(width: 8),
                                    Text(
                                      'Action Service Disabled',
                                      style: _f(
                                        AuraTheme.pink300,
                                        weight: FontWeight.w600,
                                      ),
                                    ),
                                  ],
                                ),
                                const SizedBox(height: 12),
                                Text(
                                  'AURA requires accessibility permissions to interact with other apps on your screen.',
                                  style: _f(AuraTheme.textSecondary, size: 13),
                                ),
                                const SizedBox(height: 16),
                                Row(
                                  children: [
                                    Expanded(
                                      child: _glassButton(
                                        icon: Icons.accessibility_new_rounded,
                                        label: 'Settings',
                                        onTap: _openAccessibilitySettings,
                                      ),
                                    ),
                                    const SizedBox(width: 8),
                                    Expanded(
                                      child: _glassButton(
                                        icon: Icons.refresh_rounded,
                                        label: 'Refresh',
                                        onTap: _checkServiceStatus,
                                      ),
                                    ),
                                  ],
                                ),
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
        ),

        // Text Entry Field
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 20),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(30),
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 15, sigmaY: 15),
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 8,
                ),
                decoration: BoxDecoration(
                  color: AuraTheme.bgOverlay.withOpacity(0.5),
                  borderRadius: BorderRadius.circular(30),
                  border: Border.all(
                    color: Colors.white.withOpacity(0.08),
                    width: 1,
                  ),
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
                          color: AuraTheme.bgMuted.withOpacity(0.5),
                        ),
                        child: const Icon(
                          Icons.arrow_upward_rounded,
                          color: AuraTheme.textPrimary,
                          size: 20,
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
                        ),
                        child: const Icon(
                          Icons.mic_rounded,
                          color: AuraTheme.pink400,
                          size: 20,
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

  Widget _buildVoiceMode() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 20),
      child: Column(
        children: [
          // 1. Top Container (Logo & Name)
          Expanded(
            flex: 3,
            child: _buildVoiceGlassContainer(
              borderRadius: 32,
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  AnimatedBuilder(
                    animation: _pulseCtrl,
                    builder: (context, child) {
                      return Transform.rotate(
                        angle: _pulseCtrl.value * 2 * 3.14159,
                        child: Icon(
                          Icons.blur_on_rounded,
                          size: 70 + (_pulseCtrl.value * 20),
                          color: AuraTheme.pink400.withOpacity(
                            0.6 + (_pulseCtrl.value * 0.4),
                          ),
                        ),
                      );
                    },
                  ),
                  const SizedBox(height: 16),
                  Text(
                    'AURA',
                    style: _f(
                      AuraTheme.textPrimary,
                      size: 22,
                      weight: FontWeight.w600,
                      spacing: 3,
                    ),
                  ),
                ],
              ),
            ),
          ),

          const SizedBox(height: 12),

          // 2. Middle Container (Status, X, Settings)
          _buildVoiceGlassContainer(
            borderRadius: 50, // More rounded corners per request
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
                    padding: const EdgeInsets.all(12),
                    color: Colors.transparent,
                    child: const Icon(
                      Icons.close_rounded,
                      color: AuraTheme.textSecondary,
                      size: 24,
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
                        size: 16,
                        weight: FontWeight.w500,
                      ),
                    );
                  },
                ),

                GestureDetector(
                  onTap: () => setState(() => _showSettings = true),
                  child: Container(
                    padding: const EdgeInsets.all(12),
                    color: Colors.transparent,
                    child: const Icon(
                      Icons.settings_rounded,
                      color: AuraTheme.textSecondary,
                      size: 24,
                    ),
                  ),
                ),
              ],
            ),
          ),

          const SizedBox(height: 12),

          // 3. Bottom Container (Transcription & Waveform)
          Expanded(
            flex: 5,
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
                            size: 26,
                            weight: FontWeight.w300,
                            height: 1.4,
                          ),
                          textAlign: TextAlign.center,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),

                  // Waveform and Record Button
                  Expanded(
                    flex: 3,
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        Flexible(
                          child: AnimatedBuilder(
                            animation: _pulseCtrl,
                            builder: (context, child) {
                              return Row(
                                mainAxisAlignment: MainAxisAlignment.center,
                                crossAxisAlignment: CrossAxisAlignment.end,
                                children: List.generate(15, (index) {
                                  double height =
                                      20.0 +
                                      (index % 3 == 0 ? 30 : 10) *
                                          _pulseCtrl.value;
                                  return Container(
                                    margin: const EdgeInsets.symmetric(
                                      horizontal: 4,
                                    ),
                                    width: 4,
                                    height: height,
                                    decoration: BoxDecoration(
                                      color: AuraTheme.pink400.withOpacity(0.6),
                                      borderRadius: BorderRadius.circular(2),
                                    ),
                                  );
                                }),
                              );
                            },
                          ),
                        ),
                        const SizedBox(height: 20),
                        GestureDetector(
                          onTap: _toggleRecording,
                          child: AnimatedBuilder(
                            animation: _pulseCtrl,
                            builder: (context, child) {
                              return Container(
                                padding: const EdgeInsets.all(18),
                                decoration: BoxDecoration(
                                  shape: BoxShape.circle,
                                  color:
                                      _isRecording
                                          ? AuraTheme.pink500
                                          : AuraTheme.bgMuted,
                                  boxShadow: [
                                    BoxShadow(
                                      color: (_isRecording
                                              ? AuraTheme.pink500
                                              : Colors.transparent)
                                          .withOpacity(0.4 * _pulseCtrl.value),
                                      blurRadius: 20,
                                      spreadRadius: 10,
                                    ),
                                  ],
                                ),
                                child: Icon(
                                  _isRecording
                                      ? Icons.stop_rounded
                                      : Icons.mic_rounded,
                                  color: AuraTheme.textPrimary,
                                  size: 32,
                                ),
                              );
                            },
                          ),
                        ),
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

  Widget _glassButton({
    required IconData icon,
    required String label,
    required VoidCallback onTap,
  }) => GestureDetector(
    onTap: onTap,
    child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.05),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withOpacity(0.1), width: 1.5),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, color: Colors.white.withOpacity(0.8), size: 18),
          const SizedBox(width: 8),
          Text(
            label,
            style: _f(
              Colors.white.withOpacity(0.8),
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
              child: Container(color: Colors.black.withOpacity(0.3)),
            ),
          ),
        AnimatedPositioned(
          duration: const Duration(milliseconds: 280),
          curve: Curves.easeInOutCubic,
          left: _isSidebarOpen ? 0 : -280,
          top: 0,
          bottom: 0,
          child: ClipRRect(
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 30, sigmaY: 30),
              child: Container(
                width: 280,
                decoration: BoxDecoration(
                  color: AuraTheme.bgElevated.withOpacity(0.6),
                  border: Border(
                    right: BorderSide(color: Colors.white.withOpacity(0.08)),
                  ),
                ),
                child: SafeArea(
                  child: Column(
                    children: [
                      Padding(
                        padding: const EdgeInsets.all(20),
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text(
                              'AURA',
                              style: _f(
                                Colors.white,
                                weight: FontWeight.w600,
                                size: 17,
                                spacing: 4,
                              ),
                            ),
                            GestureDetector(
                              onTap:
                                  () => setState(() => _isSidebarOpen = false),
                              child: Icon(
                                Icons.close_rounded,
                                color: Colors.white.withOpacity(0.6),
                                size: 20,
                              ),
                            ),
                          ],
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 16),
                        child: GestureDetector(
                          onTap:
                              () => setState(() {
                                _responseText = '';
                                _transcribedText = '';
                                _textCtrl.clear();
                                _chatMode = false;
                                _isSidebarOpen = false;
                              }),
                          child: _sidebarRow(Icons.edit_square, 'New chat'),
                        ),
                      ),
                      const Spacer(),
                      Padding(
                        padding: const EdgeInsets.all(16),
                        child: GestureDetector(
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

  Widget _sidebarRow(IconData icon, String label) => AnimatedContainer(
    duration: const Duration(milliseconds: 200),
    padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 14),
    decoration: BoxDecoration(
      color: Colors.white.withOpacity(0.03),
      borderRadius: BorderRadius.circular(12),
      border: Border.all(color: Colors.white.withOpacity(0.05)),
    ),
    child: Row(
      children: [
        Icon(icon, color: Colors.white.withOpacity(0.8), size: 16),
        const SizedBox(width: 12),
        Text(
          label,
          style: _f(
            Colors.white.withOpacity(0.9),
            size: 14,
            weight: FontWeight.w500,
          ),
        ),
      ],
    ),
  );

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
            borderRadius: BorderRadius.circular(24),
            child: BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 40, sigmaY: 40),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 300),
                curve: Curves.fastOutSlowIn,
                width: MediaQuery.of(context).size.width * 0.92,
                height: MediaQuery.of(context).size.height * 0.85,
                decoration: BoxDecoration(
                  color: AuraTheme.bgSurface.withOpacity(0.6),
                  borderRadius: BorderRadius.circular(24),
                  border: Border.all(
                    color: Colors.white.withOpacity(0.1),
                    width: 1.5,
                  ),
                  boxShadow: [
                    BoxShadow(
                      color: Colors.black.withOpacity(0.5),
                      blurRadius: 30,
                      spreadRadius: -5,
                    ),
                  ],
                ),
                child: Column(
                  children: [
                    // Header
                    Padding(
                      padding: const EdgeInsets.all(20),
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            'Settings',
                            style: _f(
                              Colors.white.withOpacity(0.95),
                              size: 18,
                              weight: FontWeight.w600,
                            ),
                          ),
                          GestureDetector(
                            onTap: () => setState(() => _showSettings = false),
                            child: Container(
                              padding: const EdgeInsets.all(8),
                              decoration: BoxDecoration(
                                color: Colors.white.withOpacity(0.05),
                                borderRadius: BorderRadius.circular(10),
                              ),
                              child: const Icon(
                                Icons.close_rounded,
                                color: Colors.white,
                                size: 18,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),

                    // Nav Tabs
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 20),
                      child: Container(
                        decoration: BoxDecoration(
                          color: Colors.black.withOpacity(0.2),
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(
                            color: Colors.white.withOpacity(0.05),
                          ),
                        ),
                        child: Row(
                          children: [
                            Expanded(
                              child: _buildSettingsTab(
                                0,
                                'Profile',
                                Icons.person_rounded,
                              ),
                            ),
                            Expanded(
                              child: _buildSettingsTab(
                                1,
                                'Memory',
                                Icons.memory_rounded,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),

                    const SizedBox(height: 20),

                    // Content Area
                    Expanded(
                      child: AnimatedSwitcher(
                        duration: const Duration(milliseconds: 250),
                        child:
                            _activeSettingsSection == 0
                                ? _buildProfileSettings()
                                : _buildMemorySettings(),
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

  Widget _buildSettingsTab(int index, String label, IconData icon) {
    bool isActive = _activeSettingsSection == index;
    return GestureDetector(
      onTap: () => setState(() => _activeSettingsSection = index),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.symmetric(vertical: 10),
        decoration: BoxDecoration(
          color: isActive ? Colors.white.withOpacity(0.1) : Colors.transparent,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              icon,
              size: 16,
              color: isActive ? Colors.white : AuraTheme.textSecondary,
            ),
            const SizedBox(width: 8),
            Text(
              label,
              style: _f(
                isActive ? Colors.white : AuraTheme.textSecondary,
                size: 13,
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
          _buildSettingsInput('Username', _userName),
          const SizedBox(height: 16),
          _buildSettingsInput('Email', 'user@example.com'),
          const SizedBox(height: 16),
          _buildSettingsDropdown('Theme', 'Dark'),
          const SizedBox(height: 16),
          _buildSettingsDropdown('Voice', 'Gacrux'),
          const SizedBox(height: 30),
          _buildActionBtn('Save Changes', AuraTheme.pink500, () {}),
          const SizedBox(height: 30),
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
                child: _buildMemoryStatCard(
                  'Total preferences',
                  '24',
                  AuraTheme.pink400,
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: _buildMemoryStatCard(
                  'Personal info',
                  '8',
                  AuraTheme.pink300,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: _buildMemoryStatCard(
                  'App preferences',
                  '16',
                  AuraTheme.pink200,
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: _buildMemoryStatCard(
                  'Storage used',
                  '1.2 MB',
                  AuraTheme.textSecondary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 30),
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: Colors.white.withOpacity(0.03),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: Colors.white.withOpacity(0.05)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Manage Memory',
                  style: _f(Colors.white, size: 15, weight: FontWeight.w600),
                ),
                const SizedBox(height: 8),
                Text(
                  'Clearing memory will remove all stored preferences and context.',
                  style: _f(AuraTheme.textSecondary, size: 13, height: 1.4),
                ),
                const SizedBox(height: 16),
                _buildActionBtn(
                  'Clear Memory',
                  Colors.redAccent.withOpacity(0.8),
                  () {},
                ),
              ],
            ),
          ),
          const SizedBox(height: 30),
        ],
      ),
    );
  }

  Widget _buildSettingsInput(String label, String placeholder) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: _f(AuraTheme.textSecondary, size: 13, weight: FontWeight.w500),
        ),
        const SizedBox(height: 8),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          decoration: BoxDecoration(
            color: Colors.black.withOpacity(0.3),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: Colors.white.withOpacity(0.08)),
          ),
          child: Row(
            children: [
              Expanded(
                child: Text(placeholder, style: _f(Colors.white, size: 14)),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildSettingsDropdown(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: _f(AuraTheme.textSecondary, size: 13, weight: FontWeight.w500),
        ),
        const SizedBox(height: 8),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          decoration: BoxDecoration(
            color: Colors.black.withOpacity(0.3),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: Colors.white.withOpacity(0.08)),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(value, style: _f(Colors.white, size: 14)),
              Icon(
                Icons.keyboard_arrow_down_rounded,
                color: AuraTheme.textSecondary,
                size: 18,
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildMemoryStatCard(String label, String value, Color color) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.black.withOpacity(0.2),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withOpacity(0.05)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(value, style: _f(color, size: 24, weight: FontWeight.w700)),
          const SizedBox(height: 4),
          Text(
            label,
            style: _f(
              AuraTheme.textSecondary,
              size: 12,
              weight: FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildActionBtn(String label, Color color, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(vertical: 14),
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: color,
          borderRadius: BorderRadius.circular(12),
          boxShadow: [
            BoxShadow(
              color: color.withOpacity(0.3),
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
import 'dart:convert';
import 'dart:io';

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
}

class AutomationDemo extends StatefulWidget {
  const AutomationDemo({super.key});
  @override
  State<AutomationDemo> createState() => _AutomationDemoState();
}

class _AutomationDemoState extends State<AutomationDemo>
    with TickerProviderStateMixin {
  static const _platform = MethodChannel('com.example.automation/service');

  // ignore: unused_field
  String _status = 'Waiting...';
  bool _serviceEnabled = false;
  bool _isLoading = false;
  bool _isRecording = false;
  bool _isSidebarOpen = false;
  bool _chatMode = false;
  bool _showSettings = false;

  final TextEditingController _textCtrl = TextEditingController();
  String _responseText = '';
  String _transcribedText = '';
  bool _isThinking = false;
  final String _userName = 'User';

  HttpServer? _actionServer;
  late AnimationController _pulseCtrl;
  late AnimationController _thinkCtrl;
  late VideoPlayerController _videoCtrl;
  final AudioPlayer _audioPlayer = AudioPlayer();

  @override
  void initState() {
    super.initState();
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

    _videoCtrl = VideoPlayerController.asset('assets/Background3.mp4')
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
          'session_id': 'flutter_${DateTime.now().millisecondsSinceEpoch}',
          'user_id': 'flutter_user',
          'device_type': 'mobile',
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
        backgroundColor: AuraTheme.bgBase,
        body: Stack(
          children: [
            SafeArea(
              child: Column(
                children: [
                  const SizedBox(height: 20),
                  Padding(
                    padding: const EdgeInsets.only(
                      top: 60,
                      left: 20,
                      right: 20,
                    ),
                    child: Column(
                      children: [
                        Text(
                          _greeting(),
                          style: _f(
                            Colors.white.withOpacity(0.50),
                            size: 13,
                            weight: FontWeight.w400,
                          ),
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 10),
                        Text(
                          'What would you like done today?',
                          style: _f(
                            Colors.white,
                            size: 22,
                            weight: FontWeight.w600,
                            spacing: -0.4,
                          ),
                          textAlign: TextAlign.center,
                        ),
                      ],
                    ),
                  ),
                  const Spacer(),

                  if (_transcribedText.isNotEmpty && !_isThinking) ...[
                    _infoCard(icon: Icons.mic_rounded, text: _transcribedText),
                    const SizedBox(height: 12),
                  ],

                  if (_isThinking) ...[
                    AnimatedBuilder(
                      animation: _thinkCtrl,
                      builder:
                          (_, __) => _infoCard(
                            icon: null,
                            text: 'Thinking...',
                            spinner: true,
                            spinV: _thinkCtrl.value,
                          ),
                    ),
                    const SizedBox(height: 12),
                  ],

                  if (_responseText.isNotEmpty && !_isThinking) ...[
                    _infoCard(icon: Icons.auto_awesome, text: _responseText),
                    const SizedBox(height: 12),
                  ],

                  const Spacer(),

                  if (!_serviceEnabled)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 14),
                      child: Column(
                        children: [
                          _flatButton(
                            icon: Icons.accessibility_new_rounded,
                            label: 'Enable Accessibility Service',
                            onTap: _openAccessibilitySettings,
                            primary: true,
                          ),
                          const SizedBox(height: 8),
                          _flatButton(
                            icon: Icons.refresh_rounded,
                            label: 'Refresh Status',
                            onTap: _checkServiceStatus,
                            primary: false,
                          ),
                        ],
                      ),
                    ),

                  _buildVoiceControls(),
                  const SizedBox(height: 90),
                ],
              ),
            ),

            _buildSidebar(),
            if (_showSettings) _buildSettingsModal(),

            if (!_isSidebarOpen && !_showSettings)
              Positioned(
                top: 50,
                left: 20,
                child: GestureDetector(
                  onTap: () => setState(() => _isSidebarOpen = true),
                  child: Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: AuraTheme.bgElevated.withOpacity(0.55),
                      borderRadius: BorderRadius.circular(11),
                    ),
                    child: const Icon(
                      Icons.menu_rounded,
                      color: Colors.white,
                      size: 22,
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  // ── helpers ────────────────────────────────────────────────────────────────

  Widget _infoCard({
    IconData? icon,
    required String text,
    bool spinner = false,
    double spinV = 0,
  }) => Container(
    alignment: Alignment.topLeft,
    margin: const EdgeInsets.symmetric(horizontal: 20),
    padding: const EdgeInsets.all(14),
    decoration: BoxDecoration(
      color: AppColors.darkPlum4.withOpacity(0.55),
      borderRadius: BorderRadius.circular(15),
      border: Border.all(color: AuraTheme.pink400.withOpacity(0.18)),
    ),
    child: Row(
      children: [
        if (spinner)
          SizedBox(
            width: 17,
            height: 17,
            child: CircularProgressIndicator(
              strokeWidth: 1.7,
              valueColor: AlwaysStoppedAnimation(
                AuraTheme.pink400.withOpacity(0.55 + spinV * 0.4),
              ),
            ),
          )
        else if (icon != null)
          Icon(icon, color: AuraTheme.pink300, size: 17),
        const SizedBox(width: 11),
        Expanded(
          child: Text(
            text,
            style: _f(Colors.white.withOpacity(0.88), size: 13, height: 1.52),
          ),
        ),
      ],
    ),
  );

  Widget _flatButton({
    required IconData icon,
    required String label,
    required VoidCallback onTap,
    required bool primary,
  }) => GestureDetector(
    onTap: onTap,
    child: Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      decoration: BoxDecoration(
        // Flat colours — no gradient — GLES2-safe
        color: AuraTheme.bgElevated.withOpacity(0.5),
        borderRadius: BorderRadius.circular(11),
        border: Border.all(
          color:
              primary
                  ? AuraTheme.pink400.withOpacity(0.28)
                  : Colors.white.withOpacity(0.10),
          width: 1.1,
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            icon,
            color: primary ? AuraTheme.pink400 : Colors.white.withOpacity(0.55),
            size: 16,
          ),
          const SizedBox(width: 8),
          Text(
            label,
            style: _f(
              primary ? AuraTheme.pink400 : Colors.white.withOpacity(0.60),
              size: 12,
              weight: FontWeight.w500,
            ),
          ),
        ],
      ),
    ),
  );

  // ── Voice controls ─────────────────────────────────────────────────────────

  Widget _buildVoiceControls() {
    if (_chatMode) {
      return Container(
        margin: const EdgeInsets.symmetric(horizontal: 20),
        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
        decoration: BoxDecoration(
          color: AuraTheme.bgElevated.withOpacity(0.7),
          borderRadius: BorderRadius.circular(28),
          border: Border.all(color: Colors.white.withOpacity(0.07), width: 1),
        ),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _textCtrl,
                style: _f(Colors.white, size: 14),
                decoration: InputDecoration(
                  hintText: 'Type your message...',
                  hintStyle: _f(Colors.white.withOpacity(0.30), size: 14),
                  border: InputBorder.none,
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 12,
                  ),
                ),
                onSubmitted: (_) => _sendTextToBackend(_textCtrl.text),
              ),
            ),
            _circleBtn(
              icon: Icons.arrow_upward_rounded,
              onTap: () => _sendTextToBackend(_textCtrl.text),
            ),
            const SizedBox(width: 4),
            _circleBtn(
              icon: Icons.mic_rounded,
              onTap: () => setState(() => _chatMode = false),
            ),
            const SizedBox(width: 4),
          ],
        ),
      );
    }

    return AnimatedBuilder(
      animation: _pulseCtrl,
      builder: (_, __) {
        final pv = _pulseCtrl.value;
        return Container(
          margin: const EdgeInsets.symmetric(horizontal: 46),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          decoration: BoxDecoration(
            color:
                _isRecording
                    ? AuraTheme.bgElevated.withOpacity(0.8)
                    : AppColors.darkPlum4.withOpacity(0.65),
            borderRadius: BorderRadius.circular(32),
            border: Border.all(
              color:
                  _isRecording
                      ? AuraTheme.pink400.withOpacity(0.32 + pv * 0.28)
                      : Colors.white.withOpacity(0.10),
              width: 1.2,
            ),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            mainAxisSize: MainAxisSize.min,
            children: [
              _circleBtn(
                icon: Icons.close_rounded,
                small: true,
                onTap: () => setState(() => _chatMode = true),
              ),
              const SizedBox(width: 10),

              // Main mic button — flat color, no gradient
              GestureDetector(
                onTap: _toggleRecording,
                child: Container(
                  width: 66,
                  height: 66,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    // Flat solid colour — GLES2-safe
                    color:
                        _isRecording
                            ? AuraTheme.pink500
                            : AuraTheme.pink400Dull.withOpacity(0.35),
                    border: Border.all(
                      color:
                          _isRecording
                              ? Colors.transparent
                              : Colors.white.withOpacity(0.14),
                      width: 1.2,
                    ),
                  ),
                  child: Icon(
                    _isRecording ? Icons.stop_rounded : Icons.mic_rounded,
                    size: 30,
                    color:
                        _isRecording
                            ? Colors.white
                            : Colors.white.withOpacity(0.72),
                  ),
                ),
              ),

              const SizedBox(width: 10),
              _circleBtn(
                icon: Icons.settings_rounded,
                small: true,
                onTap: () => setState(() => _showSettings = true),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _circleBtn({
    required IconData icon,
    bool small = false,
    required VoidCallback onTap,
  }) {
    final sz = small ? 40.0 : 44.0;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: sz,
        height: sz,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: Colors.white.withOpacity(0.07),
          border: Border.all(color: Colors.white.withOpacity(0.12)),
        ),
        child: Icon(
          icon,
          color: Colors.white.withOpacity(0.75),
          size: small ? 17 : 19,
        ),
      ),
    );
  }

  // ── Sidebar ────────────────────────────────────────────────────────────────

  Widget _buildSidebar() {
    return AnimatedPositioned(
      duration: const Duration(milliseconds: 280),
      curve: Curves.easeInOutCubic,
      left: _isSidebarOpen ? 0 : -280,
      top: 0,
      bottom: 0,
      child: Container(
        width: 280,
        color: AuraTheme.bgSurface,
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
                      onTap: () => setState(() => _isSidebarOpen = false),
                      child: const Icon(
                        Icons.close_rounded,
                        color: Colors.white,
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
                  child: _sidebarRow(Icons.settings_rounded, 'Settings'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _sidebarRow(IconData icon, String label) => Container(
    padding: const EdgeInsets.symmetric(vertical: 11, horizontal: 13),
    decoration: BoxDecoration(
      color: AuraTheme.bgElevated.withOpacity(0.4),
      borderRadius: BorderRadius.circular(10),
      border: Border.all(color: Colors.white.withOpacity(0.06)),
    ),
    child: Row(
      children: [
        Icon(icon, color: Colors.white.withOpacity(0.70), size: 16),
        const SizedBox(width: 11),
        Text(
          label,
          style: _f(
            Colors.white.withOpacity(0.85),
            size: 13,
            weight: FontWeight.w500,
          ),
        ),
      ],
    ),
  );

  // ── Settings modal ─────────────────────────────────────────────────────────

  Widget _buildSettingsModal() => Container(
    color: Colors.black.withOpacity(0.80),
    child: Center(
      child: Container(
        width: MediaQuery.of(context).size.width * 0.92,
        height: MediaQuery.of(context).size.height * 0.82,
        decoration: BoxDecoration(
          color: AuraTheme.bgSurface,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: Colors.white.withOpacity(0.06)),
        ),
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    'Settings',
                    style: _f(
                      Colors.white.withOpacity(0.9),
                      size: 17,
                      weight: FontWeight.w600,
                    ),
                  ),
                  GestureDetector(
                    onTap: () => setState(() => _showSettings = false),
                    child: Container(
                      padding: const EdgeInsets.all(7),
                      decoration: BoxDecoration(
                        color: AuraTheme.bgElevated.withOpacity(0.4),
                        borderRadius: BorderRadius.circular(9),
                      ),
                      child: const Icon(
                        Icons.close_rounded,
                        color: Colors.white,
                        size: 17,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            Expanded(
              child: Center(
                child: Text(
                  'Settings content',
                  style: _f(AuraTheme.textMuted, size: 13),
                ),
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

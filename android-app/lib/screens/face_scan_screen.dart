// lib/screens/face_scan_screen.dart
import 'dart:convert';
import 'dart:io';
import 'dart:ui';
import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:video_player/video_player.dart';
import '../theme.dart';

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

class FaceScanScreen extends StatefulWidget {
  final String title;
  final String subtitle;

  const FaceScanScreen({
    super.key,
    this.title = 'Face Scan',
    this.subtitle = 'Look directly at the camera',
  });

  @override
  State<FaceScanScreen> createState() => _FaceScanScreenState();
}

class _FaceScanScreenState extends State<FaceScanScreen>
    with TickerProviderStateMixin {
  CameraController? _cameraController;
  List<CameraDescription> _cameras = [];
  bool _cameraReady = false;
  bool _scanning = false;
  String? _error;
  bool _cardVisible = false;
  bool _rotatePreviewQuarterTurn = false;

  late AnimationController _pulseCtrl;
  late VideoPlayerController _videoCtrl;
  static const _tts = MethodChannel('com.example.automation/tts');
  final FlutterTts _fallbackTts = FlutterTts();

  @override
  void initState() {
    super.initState();
    _fallbackTts.setLanguage('en-US').catchError((_) {});
    _fallbackTts.setSpeechRate(0.46).catchError((_) {});
    _fallbackTts.setPitch(1.0).catchError((_) {});
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1400),
    )..repeat(reverse: true);
    _videoCtrl =
        VideoPlayerController.asset('assets/aura.mp4')
          ..setLooping(true)
          ..initialize().then((_) {
            if (mounted) {
              setState(() {
                _cardVisible = true;
              });
              _videoCtrl.play();
            }
          });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _speak(
        '${widget.title}. ${widget.subtitle}. Tap Capture Face when your face is centered.',
      );
    });
    _initCamera();
  }

  Future<void> _speak(String text) async {
    try {
      await _tts.invokeMethod('speak', {'text': text});
    } catch (_) {
      try {
        await _fallbackTts.stop();
        await _fallbackTts.setSpeechRate(0.46);
        await _fallbackTts.setPitch(1.0);
        await _fallbackTts.speak(text);
      } catch (_) {}
    }
  }

  Future<void> _initCamera() async {
    try {
      // Check permission status first — request only if not already granted.
      var permission = await Permission.camera.status;
      if (!permission.isGranted) {
        permission = await Permission.camera.request();
      }
      if (permission != PermissionStatus.granted) {
        if (mounted) {
          setState(() {
            _error =
                'Camera permission denied. Please grant camera access in settings.';
            _cameraReady = false;
          });
        }
        return;
      }

      _cameras = await availableCameras();
      final front = _cameras.firstWhere(
        (c) => c.lensDirection == CameraLensDirection.front,
        orElse: () => _cameras.first,
      );

      _cameraController = CameraController(
        front,
        ResolutionPreset.medium,
        enableAudio: false,
        imageFormatGroup: ImageFormatGroup.jpeg,
      );
      await _cameraController!.initialize();

      if (Platform.isAndroid) {
        await _cameraController!.lockCaptureOrientation(
          DeviceOrientation.portraitUp,
        );
      }

      final previewSize = _cameraController!.value.previewSize;
      final shouldRotate =
          Platform.isAndroid &&
          previewSize != null &&
          previewSize.width > previewSize.height;

      if (mounted) {
        setState(() {
          _rotatePreviewQuarterTurn = shouldRotate;
          _cameraReady = true;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = 'Camera error: $e');
    }
  }

  Future<void> _capture() async {
    if (_scanning || _cameraController == null || !_cameraReady) return;
    setState(() {
      _scanning = true;
      _error = null;
    });
    print("📸 Capture started");

    XFile? file;
    try {
      file = await _cameraController!.takePicture().timeout(
        const Duration(seconds: 12),
      );
      print("✅ Picture taken: ${file.path}");

      final bytes = await File(
        file.path,
      ).readAsBytes().timeout(const Duration(seconds: 8));
      print("📦 Raw image size: ${bytes.length} bytes");

      final base64Image = base64Encode(bytes);
      print("✅ Base64 length: ${base64Image.length}");

      if (!mounted) return;
      Navigator.of(context).pop(base64Image);
    } catch (e) {
      print("❌ Capture/read error: $e");
      if (mounted) {
        setState(() {
          _scanning = false;
          _error = 'Capture failed. Please try again.';
        });
      }
    }
  }

  @override
  void dispose() {
    _pulseCtrl.dispose();
    _cameraController?.dispose();
    _videoCtrl.dispose();
    _fallbackTts.stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AuraTheme.bgBase,
      body: Stack(
        fit: StackFit.expand,
        children: [
          if (_videoCtrl.value.isInitialized)
            FittedBox(
              fit: BoxFit.cover,
              child: SizedBox(
                width: _videoCtrl.value.size.width,
                height: _videoCtrl.value.size.height,
                child: VideoPlayer(_videoCtrl),
              ),
            ),
          Container(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Colors.black.withOpacity(0.2),
                  Colors.black.withOpacity(0.5),
                ],
              ),
            ),
          ),
          SafeArea(
            child: Align(
              alignment: Alignment.bottomCenter,
              child: TweenAnimationBuilder<double>(
                tween: Tween(begin: 0.12, end: _cardVisible ? 0 : 0.12),
                duration: const Duration(milliseconds: 560),
                curve: Curves.easeOutCubic,
                builder:
                    (context, offsetY, child) => Transform.translate(
                      offset: Offset(0, offsetY * 220),
                      child: child,
                    ),
                child: ClipRRect(
                  borderRadius: const BorderRadius.vertical(
                    top: Radius.circular(34),
                  ),
                  child: BackdropFilter(
                    filter: ImageFilter.blur(sigmaX: 16, sigmaY: 16),
                    child: Container(
                      width: double.infinity,
                      constraints: BoxConstraints(
                        minHeight: MediaQuery.of(context).size.height * 0.9,
                        maxHeight: MediaQuery.of(context).size.height * 0.9,
                      ),
                      decoration: BoxDecoration(
                        color: Colors.black.withOpacity(0.14),
                        border: Border(
                          top: BorderSide(
                            color: Colors.white.withOpacity(0.15),
                            width: 1,
                          ),
                        ),
                      ),
                      child: SingleChildScrollView(
                        padding: const EdgeInsets.fromLTRB(24, 26, 24, 24),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.center,
                          children: [
                            Row(
                              children: [
                                GestureDetector(
                                  onTap: () => Navigator.of(context).pop(null),
                                  child: Container(
                                    padding: const EdgeInsets.all(9),
                                    decoration: BoxDecoration(
                                      color: Colors.white.withOpacity(0.08),
                                      borderRadius: BorderRadius.circular(12),
                                      border: Border.all(
                                        color: Colors.white.withOpacity(0.14),
                                      ),
                                    ),
                                    child: const Icon(
                                      Icons.arrow_back_rounded,
                                      color: Colors.white,
                                      size: 18,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 10),
                            Container(
                              width: 44,
                              height: 4,
                              decoration: BoxDecoration(
                                color: Colors.white.withOpacity(0.28),
                                borderRadius: BorderRadius.circular(999),
                              ),
                            ),
                            const SizedBox(height: 24),
                            Text(
                              widget.title,
                              style: _f(
                                AuraTheme.textPrimary,
                                size: 31,
                                weight: FontWeight.w500,
                              ),
                            ),
                            const SizedBox(height: 8),
                            Text(
                              widget.subtitle,
                              style: _f(AuraTheme.textSecondary, size: 13),
                              textAlign: TextAlign.center,
                            ),
                            const SizedBox(height: 40),
                            if (_error != null)
                              Padding(
                                padding: const EdgeInsets.only(bottom: 14),
                                child: Container(
                                  width: double.infinity,
                                  padding: const EdgeInsets.all(12),
                                  decoration: BoxDecoration(
                                    color: AuraTheme.error.withOpacity(0.12),
                                    borderRadius: BorderRadius.circular(14),
                                    border: Border.all(
                                      color: AuraTheme.error.withOpacity(0.35),
                                    ),
                                  ),
                                  child: Text(
                                    _error!,
                                    style: _f(AuraTheme.error, size: 12),
                                    textAlign: TextAlign.center,
                                  ),
                                ),
                              ),
                            AnimatedBuilder(
                              animation: _pulseCtrl,
                              builder: (_, __) {
                                final glow = 0.08 + (_pulseCtrl.value * 0.07);
                                return Container(
                                  width: double.infinity,
                                  padding: const EdgeInsets.fromLTRB(
                                    20,
                                    18,
                                    20,
                                    14,
                                  ),
                                  decoration: BoxDecoration(
                                    color: Colors.white.withOpacity(0.04),
                                    borderRadius: BorderRadius.circular(24),
                                    border: Border.all(
                                      color: Colors.white.withOpacity(0.14),
                                    ),
                                    boxShadow: [
                                      BoxShadow(
                                        color: Colors.white.withOpacity(glow),
                                        blurRadius: 18,
                                        offset: const Offset(0, 5),
                                      ),
                                    ],
                                  ),
                                  child: Column(
                                    children: [
                                      SizedBox(
                                        width: 170,
                                        height: 170,
                                        child: ClipOval(
                                          child:
                                              _error != null
                                                  ? Container(
                                                    color: AuraTheme.bgElevated,
                                                    alignment: Alignment.center,
                                                    child: Padding(
                                                      padding:
                                                          const EdgeInsets.all(
                                                            14,
                                                          ),
                                                      child: Text(
                                                        _error!,
                                                        style: _f(
                                                          AuraTheme.error,
                                                          size: 12,
                                                        ),
                                                        textAlign:
                                                            TextAlign.center,
                                                      ),
                                                    ),
                                                  )
                                                  : !_cameraReady
                                                  ? Container(
                                                    color: AuraTheme.bgElevated,
                                                    alignment: Alignment.center,
                                                    child: const SizedBox(
                                                      width: 26,
                                                      height: 26,
                                                      child:
                                                          CircularProgressIndicator(
                                                            strokeWidth: 2,
                                                            color:
                                                                AuraTheme
                                                                    .pink400,
                                                          ),
                                                    ),
                                                  )
                                                  : Builder(
                                                    builder: (_) {
                                                      Widget
                                                      preview = FittedBox(
                                                        fit: BoxFit.cover,
                                                        child: SizedBox(
                                                          width:
                                                              _cameraController!
                                                                  .value
                                                                  .previewSize!
                                                                  .height,
                                                          height:
                                                              _cameraController!
                                                                  .value
                                                                  .previewSize!
                                                                  .width,
                                                          child: CameraPreview(
                                                            _cameraController!,
                                                          ),
                                                        ),
                                                      );

                                                      // if (_rotatePreviewQuarterTurn) {
                                                      //   preview = RotatedBox(
                                                      //     quarterTurns: 1,
                                                      //     child: preview,
                                                      //   );
                                                      // }

                                                      return preview;
                                                    },
                                                  ),
                                        ),
                                      ),
                                      const SizedBox(height: 30),
                                      Text(
                                        _scanning
                                            ? 'Capturing...'
                                            : 'Position your face in the circle',
                                        style: _f(AuraTheme.pink400, size: 13),
                                      ),
                                      const SizedBox(height: 8),
                                      Text(
                                        'Your face data is encrypted and never stored as an image.',
                                        style: _f(
                                          AuraTheme.textSecondary,
                                          size: 11,
                                          weight: FontWeight.w500,
                                        ),
                                        textAlign: TextAlign.center,
                                      ),
                                    ],
                                  ),
                                );
                              },
                            ),
                            const SizedBox(height: 50),
                            GestureDetector(
                              onTap:
                                  (_scanning || !_cameraReady)
                                      ? null
                                      : _capture,
                              child: Container(
                                width: double.infinity,
                                height: 54,
                                decoration: BoxDecoration(
                                  color: Colors.white.withOpacity(0.06),
                                  borderRadius: BorderRadius.circular(28),
                                  border: Border.all(
                                    color: AuraTheme.pink400.withOpacity(0.55),
                                    width: 1.2,
                                  ),
                                ),
                                child: Center(
                                  child: Text(
                                    _scanning ? 'Capturing...' : 'Capture Face',
                                    style: _f(
                                      AuraTheme.pink300,
                                      size: 15,
                                      weight: FontWeight.w600,
                                    ),
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
              ),
            ),
          ),
        ],
      ),
    );
  }
}

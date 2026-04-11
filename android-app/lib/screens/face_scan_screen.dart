// lib/screens/face_scan_screen.dart
import 'dart:convert';
import 'dart:io';
import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';
import '../theme.dart';

TextStyle _f(Color color,
    {FontWeight weight = FontWeight.w400,
    double size = 14,
    double spacing = 0,
    double height = 1.45}) =>
    TextStyle(
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

  late AnimationController _pulseCtrl;

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1400),
    )..repeat(reverse: true);
    _initCamera();
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
            _error = 'Camera permission denied. Please grant camera access in settings.';
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
      if (mounted) setState(() => _cameraReady = true);
    } catch (e) {
      if (mounted) setState(() => _error = 'Camera error: $e');
    }
  }

  Future<void> _capture() async {
    if (_scanning || _cameraController == null || !_cameraReady) return;
    if (!mounted) return;
    setState(() {
      _scanning = true;
      _error = null;
    });

    XFile? file;
    try {
      file = await _cameraController!.takePicture();
    } catch (e) {
      if (mounted) {
        setState(() {
          _scanning = false;
          _error = 'Capture failed. Try again.';
        });
      }
      return;
    }

    try {
      final bytes = await File(file.path).readAsBytes();
      final base64Image = 'data:image/jpeg;base64,${base64Encode(bytes)}';
      if (mounted) {
        Navigator.of(context).pop(base64Image);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _scanning = false;
          _error = 'Failed to read image. Try again.';
        });
      }
    }
  }

  @override
  void dispose() {
    _pulseCtrl.dispose();
    _cameraController?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AuraTheme.bgBase,
      body: SafeArea(
        child: Column(
          children: [
            // ── Header ──────────────────────────────────────────────────────
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
              child: Row(
                children: [
                  GestureDetector(
                    onTap: () => Navigator.of(context).pop(null),
                    child: Container(
                      padding: const EdgeInsets.all(10),
                      decoration: BoxDecoration(
                        color: AuraTheme.bgElevated,
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(
                            color: Colors.white.withOpacity(0.08)),
                      ),
                      child: const Icon(Icons.arrow_back_rounded,
                          color: AuraTheme.textSecondary, size: 20),
                    ),
                  ),
                  const SizedBox(width: 16),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(widget.title,
                          style: _f(AuraTheme.textPrimary,
                              size: 18, weight: FontWeight.w600)),
                      Text(widget.subtitle,
                          style: _f(AuraTheme.textSecondary, size: 13)),
                    ],
                  ),
                ],
              ),
            ),

            // ── Camera preview in circular frame ────────────────────────────
            Expanded(
              child: Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    // FIX: CameraPreview must NOT live inside AnimatedBuilder.
                    // AnimatedBuilder rebuilds its subtree on every animation tick
                    // (~60×/sec) which races with the Android SurfaceTexture and
                    // causes the preview to stay black/blank.
                    // Solution: use a Stack — the animated border sits below,
                    // the preview sits above in a separate, stable widget slot.
                    SizedBox(
                      width: 280,
                      height: 280,
                      child: Stack(
                        alignment: Alignment.center,
                        children: [
                          // Layer 1 — animated glowing border (rebuilds freely)
                          AnimatedBuilder(
                            animation: _pulseCtrl,
                            builder: (_, __) => Container(
                              width: 280,
                              height: 280,
                              decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                border: Border.all(
                                  color: AuraTheme.pink400.withOpacity(
                                      0.4 + _pulseCtrl.value * 0.4),
                                  width: 2.5,
                                ),
                                boxShadow: [
                                  BoxShadow(
                                    color: AuraTheme.pink400.withOpacity(
                                        0.15 + _pulseCtrl.value * 0.15),
                                    blurRadius: 30,
                                    spreadRadius: 8,
                                  ),
                                ],
                              ),
                            ),
                          ),

                          // Layer 2 — camera preview (stable, never rebuilt by animation)
                          ClipOval(
                            child: SizedBox(
                              width: 274,
                              height: 274,
                              child: _error != null
                                  ? Container(
                                      color: AuraTheme.bgElevated,
                                      child: Center(
                                        child: Padding(
                                          padding: const EdgeInsets.all(16),
                                          child: Text(
                                            _error!,
                                            style: _f(AuraTheme.error, size: 13),
                                            textAlign: TextAlign.center,
                                          ),
                                        ),
                                      ),
                                    )
                                  : !_cameraReady
                                      ? Container(
                                          color: AuraTheme.bgElevated,
                                          child: Column(
                                            mainAxisAlignment:
                                                MainAxisAlignment.center,
                                            children: [
                                              const Icon(
                                                Icons.face_retouching_natural,
                                                color: AuraTheme.pink400,
                                                size: 48,
                                              ),
                                              const SizedBox(height: 12),
                                              const SizedBox(
                                                width: 24,
                                                height: 24,
                                                child:
                                                    CircularProgressIndicator(
                                                  strokeWidth: 2,
                                                  color: AuraTheme.pink400,
                                                ),
                                              ),
                                            ],
                                          ),
                                        )
                                      : FittedBox(
                                          fit: BoxFit.cover,
                                          child: SizedBox(
                                            width: _cameraController!
                                                .value
                                                .previewSize!
                                                .height,
                                            height: _cameraController!
                                                .value
                                                .previewSize!
                                                .width,
                                            child: CameraPreview(
                                                _cameraController!),
                                          ),
                                        ),
                            ),
                          ),
                        ],
                      ),
                    ),

                    const SizedBox(height: 32),

                    if (_scanning)
                      Column(
                        children: [
                          const CircularProgressIndicator(
                              color: AuraTheme.pink400),
                          const SizedBox(height: 12),
                          Text('Scanning...',
                              style: _f(AuraTheme.textSecondary, size: 14)),
                        ],
                      )
                    else
                      GestureDetector(
                        onTap: _capture,
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 48, vertical: 16),
                          decoration: BoxDecoration(
                            gradient: const LinearGradient(
                              colors: [AuraTheme.pink500, AuraTheme.pink700],
                            ),
                            borderRadius: BorderRadius.circular(30),
                            boxShadow: [
                              BoxShadow(
                                color: AuraTheme.pink500.withOpacity(0.4),
                                blurRadius: 16,
                                offset: const Offset(0, 4),
                              ),
                            ],
                          ),
                          child: Text(
                            'Scan Face',
                            style: _f(Colors.white,
                                size: 15, weight: FontWeight.w600),
                          ),
                        ),
                      ),

                    const SizedBox(height: 16),
                    Text(
                      'Your face data is encrypted and never stored as an image.',
                      style: _f(AuraTheme.textMuted, size: 12),
                      textAlign: TextAlign.center,
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
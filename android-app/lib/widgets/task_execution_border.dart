import 'dart:math' show pi;
import 'package:flutter/material.dart';

/// Animated border drawn via CustomPainter using canvas arc segments.
/// No SweepGradient decoration → avoids GL_MAX_FRAGMENT_UNIFORM_VECTORS on GLES2.
class TaskExecutionBorder extends StatefulWidget {
  final Widget child;
  final bool isExecuting;

  const TaskExecutionBorder({
    super.key,
    required this.child,
    this.isExecuting = false,
  });

  @override
  State<TaskExecutionBorder> createState() => _TaskExecutionBorderState();
}

class _TaskExecutionBorderState extends State<TaskExecutionBorder>
    with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      duration: const Duration(seconds: 3),
      vsync: this,
    );
    if (widget.isExecuting) _ctrl.repeat();
  }

  @override
  void didUpdateWidget(covariant TaskExecutionBorder old) {
    super.didUpdateWidget(old);
    if (widget.isExecuting && !old.isExecuting) {
      _ctrl.repeat();
    } else if (!widget.isExecuting && old.isExecuting) {
      _ctrl.stop();
      _ctrl.reset();
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.isExecuting) return widget.child;

    return AnimatedBuilder(
      animation: _ctrl,
      builder:
          (_, __) => CustomPaint(
            painter: _ArcBorderPainter(progress: _ctrl.value, radius: 24),
            child: Padding(
              padding: const EdgeInsets.all(3),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(21),
                child: widget.child,
              ),
            ),
          ),
    );
  }
}

/// Draws a rotating rainbow border using individually-coloured arc segments.
/// Each segment is a flat colour (no shader) — GLES2-safe.
class _ArcBorderPainter extends CustomPainter {
  final double progress;
  final double radius;

  _ArcBorderPainter({required this.progress, required this.radius});

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Rect.fromLTWH(1.5, 1.5, size.width - 3, size.height - 3);
    final paint =
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 3.0
          ..shader = LinearGradient(
            colors: const [
              Color(0xFFFF3D9A), // pink400
              Color(0xFF4DD68C), // success
              Color(0xFFFFBA44), // warning
              Color(0xFFFF3D9A), // wrap around
            ],
            stops: const [0.0, 0.33, 0.66, 1.0],
            transform: GradientRotation(progress * 2 * pi),
          ).createShader(rect)
          ..strokeJoin = StrokeJoin.round;

    final rrect = RRect.fromRectAndRadius(rect, Radius.circular(radius));
    canvas.drawRRect(rrect, paint);
  }

  @override
  bool shouldRepaint(covariant _ArcBorderPainter old) =>
      old.progress != progress;
}

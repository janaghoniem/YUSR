import 'dart:math';
import 'package:flutter/material.dart';
import '../theme.dart';

class CinematicIntro extends StatefulWidget {
  final double size;
  const CinematicIntro({super.key, this.size = 200.0});

  @override
  State<CinematicIntro> createState() => _CinematicIntroState();
}

class _CinematicIntroState extends State<CinematicIntro>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;
  List<Particle> particles = [];
  final int particleCount = 400;

  @override
  void initState() {
    super.initState();
    _initParticles();
    _controller = AnimationController(
      duration: const Duration(seconds: 10),
      vsync: this,
    )..repeat();
  }

  void _initParticles() {
    final random = Random();
    for (int i = 0; i < particleCount; i++) {
      double r =
          1.0 * pow(random.nextDouble(), 1 / 3); // Spherical distribution
      double theta = random.nextDouble() * 2 * pi;
      double phi = acos(2 * random.nextDouble() - 1);

      double x = r * sin(phi) * cos(theta);
      double y = r * sin(phi) * sin(theta);
      double z = r * cos(phi);

      // Mix Aura colors
      Color color = random.nextBool() ? AuraTheme.pink400 : AuraTheme.pink200;
      if (random.nextDouble() > 0.8) {
        color = AuraTheme.pink600;
      }

      particles.add(
        Particle(
          x: x,
          y: y,
          z: z,
          size: random.nextDouble() * 2 + 0.5,
          color: color.withOpacity(random.nextDouble() * 0.5 + 0.3),
        ),
      );
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: widget.size,
      height: widget.size,
      child: Semantics(
        label: "Aura Cinematic Particle Intro",
        child: AnimatedBuilder(
          animation: _controller,
          builder: (context, child) {
            return CustomPaint(
              painter: ParticlePainter(particles, _controller.value),
            );
          },
        ),
      ),
    );
  }
}

class Particle {
  double x, y, z;
  double size;
  Color color;
  Particle({
    required this.x,
    required this.y,
    required this.z,
    required this.size,
    required this.color,
  });
}

class ParticlePainter extends CustomPainter {
  final List<Particle> particles;
  final double animationValue;

  ParticlePainter(this.particles, this.animationValue);

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = size.width / 2.5;

    // Rotation matrices
    double angleY = animationValue * 2 * pi;
    double angleX = animationValue * pi;

    for (var p in particles) {
      // Rotate Y
      double nx = p.x * cos(angleY) - p.z * sin(angleY);
      double nz = p.x * sin(angleY) + p.z * cos(angleY);
      double ny = p.y;

      // Rotate X
      double nnx = nx;
      double nny = ny * cos(angleX) - nz * sin(angleX);
      double nnz = ny * sin(angleX) + nz * cos(angleX);

      // Project to 2D
      // Perspective projection
      double scale = 2.0 / (2.0 + nnz);
      Offset pos = Offset(
        center.dx + nnx * radius * scale,
        center.dy + nny * radius * scale,
      );

      // Size calculation based on depth
      double pSize = p.size * scale;

      // Breathing effect
      double breath = 1.0 + sin(animationValue * 4 * pi + p.x) * 0.2;
      pSize *= breath;

      final paint =
          Paint()
            ..color = p.color
            ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 1.0);

      canvas.drawCircle(pos, pSize, paint);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => true;
}

import 'dart:math';

import 'package:flutter/material.dart';

class VoiceSpectrumVisualizer extends StatelessWidget {
  final Animation<double> animation;
  final bool active;
  final int bars;
  final double height;
  final Color color;

  const VoiceSpectrumVisualizer({
    super.key,
    required this.animation,
    required this.active,
    this.bars = 18,
    this.height = 48,
    this.color = const Color(0xFFFF3D9A),
  });

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: height,
      child: AnimatedBuilder(
        animation: animation,
        builder: (_, __) {
          final t = animation.value;
          return Row(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.center,
            children: List.generate(bars, (index) {
              final phase = (index / bars) * pi * 2;
              final minAmp = active ? 8.0 : 4.0;
              final span = active ? 16.0 : 6.0;
              final wave = sin((t * pi * (active ? 2.6 : 1.4)) + phase);
              final amp = (minAmp + wave * span).clamp(3.0, height - 6);
              final glow = (0.18 + (wave + 1) / 2 * (active ? 0.55 : 0.24))
                  .clamp(0.1, 0.9);
              return Container(
                margin: const EdgeInsets.symmetric(horizontal: 1.6),
                width: 3.2,
                height: amp,
                decoration: BoxDecoration(
                  color: color.withOpacity(glow),
                  borderRadius: BorderRadius.circular(4),
                  boxShadow: [
                    BoxShadow(
                      color: color.withOpacity(active ? glow * 0.45 : 0.08),
                      blurRadius: active ? 8 : 3,
                      spreadRadius: active ? 0.5 : 0,
                    ),
                  ],
                ),
              );
            }),
          );
        },
      ),
    );
  }
}

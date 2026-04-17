import 'dart:math';
import 'dart:ui';

import 'package:flutter/material.dart';

import '../theme.dart';

class ExecutionWidget extends StatelessWidget {
  final bool visible;
  final bool minimized;
  final bool isExecuting;
  final bool isPaused;
  final bool needsAttention;
  final String title;
  final String subtitle;
  final Animation<double> animation;
  final VoidCallback onToggleMinimize;
  final VoidCallback onPauseResume;
  final VoidCallback onStop;

  const ExecutionWidget({
    super.key,
    required this.visible,
    required this.minimized,
    required this.isExecuting,
    required this.isPaused,
    required this.needsAttention,
    required this.title,
    required this.subtitle,
    required this.animation,
    required this.onToggleMinimize,
    required this.onPauseResume,
    required this.onStop,
  });

  @override
  Widget build(BuildContext context) {
    if (!visible) return const SizedBox.shrink();

    final bottomInset = MediaQuery.of(context).viewInsets.bottom;

    return Positioned(
      right: 12,
      bottom: max(12, bottomInset + 12),
      child: SafeArea(
        child: AnimatedSwitcher(
          duration: const Duration(milliseconds: 220),
          switchInCurve: Curves.easeOutCubic,
          switchOutCurve: Curves.easeInCubic,
          child:
              minimized
                  ? _buildPill(context, key: const ValueKey('pill'))
                  : _buildCard(context, key: const ValueKey('card')),
        ),
      ),
    );
  }

  Widget _buildPill(BuildContext context, {required Key key}) {
    final accent = needsAttention ? Colors.amberAccent : AuraTheme.pink400;

    return GestureDetector(
      key: key,
      onTap: onToggleMinimize,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(24),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 16, sigmaY: 16),
          child: Container(
            constraints: const BoxConstraints(minWidth: 132),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            decoration: BoxDecoration(
              color: Colors.black.withOpacity(0.48),
              borderRadius: BorderRadius.circular(24),
              border: Border.all(color: accent.withOpacity(0.42), width: 1),
              boxShadow: [
                BoxShadow(
                  color: accent.withOpacity(0.2),
                  blurRadius: 14,
                  offset: const Offset(0, 4),
                ),
              ],
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                _wave(accent, small: true),
                const SizedBox(width: 8),
                Text(
                  needsAttention
                      ? 'Action needed'
                      : (isPaused ? 'Paused' : 'Executing'),
                  style: TextStyle(
                    fontFamily: 'PlusJakartaSans',
                    color: Colors.white,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.2,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildCard(BuildContext context, {required Key key}) {
    final w = MediaQuery.of(context).size.width;
    final cardWidth = w.clamp(220.0, 300.0) * 0.78;
    final accent = needsAttention ? Colors.amberAccent : AuraTheme.pink400;

    return ClipRRect(
      key: key,
      borderRadius: BorderRadius.circular(18),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 22, sigmaY: 22),
        child: Container(
          width: cardWidth,
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          decoration: BoxDecoration(
            color: Colors.black.withOpacity(0.56),
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: accent.withOpacity(0.36), width: 1),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  _wave(accent),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontFamily: 'PlusJakartaSans',
                        color: Colors.white,
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  GestureDetector(
                    onTap: onToggleMinimize,
                    child: const Icon(
                      Icons.keyboard_arrow_down_rounded,
                      color: Colors.white70,
                      size: 18,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                subtitle,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontFamily: 'PlusJakartaSans',
                  color: Colors.white.withOpacity(0.8),
                  fontSize: 11,
                  height: 1.35,
                ),
              ),
              if (isExecuting) ...[
                const SizedBox(height: 8),
                Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: [
                    _iconButton(
                      icon:
                          isPaused
                              ? Icons.play_arrow_rounded
                              : Icons.pause_rounded,
                      onTap: onPauseResume,
                    ),
                    const SizedBox(width: 6),
                    _iconButton(
                      icon: Icons.stop_rounded,
                      onTap: onStop,
                      color: Colors.redAccent,
                    ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _iconButton({
    required IconData icon,
    required VoidCallback onTap,
    Color? color,
  }) {
    final c = color ?? Colors.white;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(6),
        decoration: BoxDecoration(
          color: c.withOpacity(0.12),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: c.withOpacity(0.28)),
        ),
        child: Icon(icon, color: c, size: 16),
      ),
    );
  }

  Widget _wave(Color color, {bool small = false}) {
    return AnimatedBuilder(
      animation: animation,
      builder: (_, __) {
        final bars = small ? 3 : 4;
        return Row(
          children: List.generate(bars, (i) {
            final h =
                (small ? 4.0 : 5.0) +
                sin((i + animation.value * 7) * 0.9) * (small ? 4 : 6);
            return Container(
              margin: const EdgeInsets.symmetric(horizontal: 1.2),
              width: small ? 2.2 : 2.6,
              height: h.clamp(3, small ? 11 : 16),
              decoration: BoxDecoration(
                color: color,
                borderRadius: BorderRadius.circular(2),
              ),
            );
          }),
        );
      },
    );
  }
}

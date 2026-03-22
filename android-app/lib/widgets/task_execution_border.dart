import 'package:flutter/material.dart';

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
  late AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      duration: const Duration(seconds: 3),
      vsync: this,
    );

    if (widget.isExecuting) {
      _controller.repeat();
    }
  }

  @override
  void didUpdateWidget(covariant TaskExecutionBorder oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.isExecuting && !oldWidget.isExecuting) {
      _controller.repeat();
    } else if (!widget.isExecuting && oldWidget.isExecuting) {
      _controller.stop();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) {
        return Container(
          decoration:
              widget.isExecuting
                  ? BoxDecoration(
                    borderRadius: BorderRadius.circular(24),
                    gradient: SweepGradient(
                      center: FractionalOffset.center,
                      startAngle: 0.0,
                      endAngle: 3.14 * 2,
                      colors: const [
                        Colors.blue,
                        Colors.cyan,
                        Colors.green,
                        Colors.yellow,
                        Colors.orange,
                        Colors.red,
                        Colors.pink,
                        Colors.purple,
                        Colors.blue,
                      ],
                      transform: GradientRotation(_controller.value * 2 * 3.14),
                    ),
                  )
                  : null,
          child: Padding(
            padding: EdgeInsets.all(widget.isExecuting ? 4.0 : 0.0),
            child: Container(
              decoration: BoxDecoration(
                color: Theme.of(context).scaffoldBackgroundColor,
                borderRadius: BorderRadius.circular(20),
              ),
              child: widget.child,
            ),
          ),
        );
      },
      child: widget.child,
    );
  }
}

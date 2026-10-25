import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../core/theme.dart';

/// 남은 대화 횟수 원형 막대. 가운데에 '남은/전체'(예: 170/200)를 쓴다.
/// 남은 몫만큼 초록 호가 차 있고, 20% 아래로 떨어지면 흙색, 다 쓰면 회색 고리만 남는다.
class UsageRing extends StatelessWidget {
  final int left;
  final int limit;
  final double size;
  final String? tooltip;

  const UsageRing({super.key, required this.left, required this.limit, this.size = 44, this.tooltip});

  @override
  Widget build(BuildContext context) {
    final ratio = limit <= 0 ? 0.0 : (left / limit).clamp(0.0, 1.0);
    final color = left <= 0
        ? AppColors.muted
        : ratio < 0.2
            ? AppColors.clay
            : AppColors.primary;
    final big = size >= 64;
    final ring = SizedBox(
      width: size,
      height: size,
      child: CustomPaint(
        painter: _RingPainter(ratio: ratio, color: color, stroke: big ? 6 : 3.5),
        child: Center(
          child: FittedBox(
            fit: BoxFit.scaleDown,
            child: Padding(
              padding: EdgeInsets.all(big ? 10 : 6),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    '$left',
                    style: TextStyle(
                      fontSize: big ? 20 : 13,
                      height: 1.05,
                      fontWeight: FontWeight.w800,
                      color: left <= 0 ? AppColors.muted : AppColors.ink,
                      fontFamilyFallback: koreanFallback,
                    ),
                  ),
                  Text(
                    '/$limit',
                    style: TextStyle(
                      fontSize: big ? 11.5 : 8.5,
                      height: 1.1,
                      color: AppColors.muted,
                      fontFamilyFallback: koreanFallback,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
    return Semantics(
      label: tooltip ?? '남은 대화 $left번, 전체 $limit번',
      child: tooltip == null
          ? ring
          : Tooltip(
              message: tooltip!,
              triggerMode: TooltipTriggerMode.tap,
              showDuration: const Duration(seconds: 6),
              child: ring,
            ),
    );
  }
}

class _RingPainter extends CustomPainter {
  final double ratio;
  final Color color;
  final double stroke;

  _RingPainter({required this.ratio, required this.color, required this.stroke});

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final r = rect.deflate(stroke / 2);
    final base = Paint()
      ..color = AppColors.sunk
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke;
    canvas.drawArc(r, 0, math.pi * 2, false, base);
    if (ratio <= 0) return;
    final arc = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke
      ..strokeCap = StrokeCap.round;
    // 12시 방향에서 시계 방향으로 남은 몫만큼
    canvas.drawArc(r, -math.pi / 2, math.pi * 2 * ratio, false, arc);
  }

  @override
  bool shouldRepaint(_RingPainter old) => old.ratio != ratio || old.color != color || old.stroke != stroke;
}

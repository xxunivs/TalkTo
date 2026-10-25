import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';

import '../../core/theme.dart';

/// 한국어를 단어 중간에서 끊지 않게(웹 CSS 의 word-break: keep-all). 글자 사이에 보이지 않는
/// 이음표(U+2060)를 넣어 띄어쓰기에서만 줄이 바뀐다. 화면에 보이는 글에만 쓴다(복사·저장 X).
String keepAll(String text) => text
    .split(' ')
    .map((word) => word.characters.join('\u2060'))
    .join(' ');

/// TalkTo 글자 로고(랜딩페이지와 같은 모양).
class BrandLogo extends StatelessWidget {
  final double height;
  final Color color;

  const BrandLogo({super.key, this.height = 20, this.color = AppColors.ink});

  @override
  Widget build(BuildContext context) {
    return SvgPicture.asset(
      'assets/brand/talkto_logo.svg',
      height: height,
      colorFilter: ColorFilter.mode(color, BlendMode.srcIn),
      semanticsLabel: 'TalkTo',
    );
  }
}

/// 가운데 정렬된 본문 폭(넓은 화면에서 줄이 너무 길지 않게).
class PageBody extends StatelessWidget {
  final Widget child;
  final double maxWidth;

  const PageBody({super.key, required this.child, this.maxWidth = 640});

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxWidth),
        child: child,
      ),
    );
  }
}

class AppCard extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  final Color? color;
  final Color? borderColor;
  final VoidCallback? onTap;

  const AppCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(20),
    this.color,
    this.borderColor,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final radius = BorderRadius.circular(18);
    return Material(
      color: color ?? AppColors.card,
      shape: RoundedRectangleBorder(
        borderRadius: radius,
        side: BorderSide(color: borderColor ?? AppColors.line),
      ),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(padding: padding, child: SizedBox(width: double.infinity, child: child)),
      ),
    );
  }
}

/// 화면 제목(명조) + 한두 줄 설명.
class Headline extends StatelessWidget {
  final String title;
  final String? lead;
  final String? eyebrow;

  const Headline(this.title, {super.key, this.lead, this.eyebrow});

  @override
  Widget build(BuildContext context) {
    final wide = Breakpoints.isWide(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (eyebrow != null) ...[
          Text(eyebrow!, style: AppText.small.copyWith(color: AppColors.primary, fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
        ],
        Text(keepAll(title), style: wide ? AppText.display : AppText.display.copyWith(fontSize: 25)),
        if (lead != null) ...[
          const SizedBox(height: 10),
          Text(keepAll(lead!), style: AppText.lead),
        ],
      ],
    );
  }
}

/// 둥근 선택 칩. 고르면 초록으로 채운다.
class Pill extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback? onTap;
  final bool wide;
  final Color? tint;
  final IconData? icon;

  const Pill({
    super.key,
    required this.label,
    this.selected = false,
    this.onTap,
    this.wide = false,
    this.tint,
    this.icon,
  });

  @override
  Widget build(BuildContext context) {
    final background = selected ? AppColors.primary : (tint ?? Colors.white);
    final radius = BorderRadius.circular(wide ? 14 : 999);
    return Material(
      color: background,
      shape: RoundedRectangleBorder(
        borderRadius: radius,
        side: BorderSide(
          color: selected ? AppColors.primary : (tint != null ? Colors.transparent : AppColors.line2),
        ),
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: radius,
        child: Padding(
          padding: EdgeInsets.symmetric(horizontal: wide ? 16 : 15, vertical: wide ? 14 : 9),
          child: Row(
            mainAxisSize: wide ? MainAxisSize.max : MainAxisSize.min,
            children: [
              if (icon != null) ...[
                Icon(icon, size: 16, color: selected ? Colors.white : AppColors.ink2),
                const SizedBox(width: 6),
              ],
              Flexible(
                child: Text(
                  keepAll(label),
                  style: TextStyle(
                    fontSize: 15,
                    height: 1.35,
                    fontWeight: selected ? FontWeight.w700 : FontWeight.w400,
                    color: selected ? Colors.white : AppColors.ink,
                  ),
                ),
              ),
              if (wide && selected) ...[
                const SizedBox(width: 8),
                const Icon(Icons.check_rounded, size: 18, color: Colors.white),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// 하나만 고르는 칩 묶음. 선지가 길면 세로로 쌓는다.
/// [label] 이 있으면 칩에는 그 글을 보이고, 고르기·비교·[onChanged] 는 원래 값([options])으로 한다.
class ChoiceGroup extends StatelessWidget {
  final List<String> options;
  final String? value;
  final ValueChanged<String> onChanged;
  final String Function(String option)? label;

  const ChoiceGroup({super.key, required this.options, this.value, required this.onChanged, this.label});

  @override
  Widget build(BuildContext context) {
    String shown(String o) => label?.call(o) ?? o;
    final long = options.any((o) => shown(o).length > 14);
    final pills = [
      for (final o in options)
        Pill(label: shown(o), selected: o == value, wide: long, onTap: () => onChanged(o)),
    ];
    if (long) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [for (final p in pills) Padding(padding: const EdgeInsets.only(bottom: 8), child: p)],
      );
    }
    return Wrap(spacing: 8, runSpacing: 8, children: pills);
  }
}

/// 만들기 단계 표시: "2 / 4  녹음 올리기" 와 얇은 막대.
class StepProgress extends StatelessWidget {
  /// 0부터. names.length 와 같으면 다 끝남.
  final int current;

  const StepProgress(this.current, {super.key});

  static const names = ['기본 정보', '녹음 올리기', '그분 목소리 찾기', '질문 답하기'];

  @override
  Widget build(BuildContext context) {
    final index = current.clamp(0, names.length - 1);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text.rich(
          TextSpan(
            text: '${index + 1} / ${names.length}  ',
            style: const TextStyle(fontWeight: FontWeight.w700, color: AppColors.primary, fontSize: 13),
            children: [
              TextSpan(
                text: names[index],
                style: const TextStyle(fontWeight: FontWeight.w600, color: AppColors.ink2),
              ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            for (var i = 0; i < names.length; i++)
              Expanded(
                child: Container(
                  height: 4,
                  margin: EdgeInsets.only(right: i == names.length - 1 ? 0 : 4),
                  decoration: BoxDecoration(
                    color: i <= index ? AppColors.primary : AppColors.line,
                    borderRadius: BorderRadius.circular(4),
                  ),
                ),
              ),
          ],
        ),
      ],
    );
  }
}

/// 만들기 단계 화면 틀. 위에 단계, 가운데 내용, 아래에 늘 같은 자리의 버튼 줄.
/// 내용 맨 아래에는 바닥 글(개인정보 처리방침 링크 등)이 붙는다: [footer], 없으면 [defaultFooter].
class WizardScaffold extends StatelessWidget {
  final String? title;
  final int? step;
  final String headline;
  final String? lead;
  final List<Widget> children;
  final Widget? primary;
  final Widget? secondary;
  final List<Widget>? actions;
  final ScrollController? controller;

  /// 이 화면만의 바닥 글. 없으면 [defaultFooter].
  final Widget? footer;

  /// 정보를 받는 화면(만들기·녹음·가족 공유·동의 녹음 등) 아래에 늘 붙는 바닥 글. 앱이 시작할 때 정한다
  /// (main.dart: 개인정보 처리방침 · 이용약관 · 사업자 정보 한 줄). shared 가 features 를 가져오지 않게
  /// 여기서는 만드는 법만 받는다. 시험에서는 비워 둔다(없으면 붙이지 않는다).
  static WidgetBuilder? defaultFooter;

  const WizardScaffold({
    super.key,
    this.title,
    this.step,
    required this.headline,
    this.lead,
    required this.children,
    this.primary,
    this.secondary,
    this.actions,
    this.controller,
    this.footer,
  });

  @override
  Widget build(BuildContext context) {
    final wide = Breakpoints.isWide(context);
    final bottom = footer ?? defaultFooter?.call(context);
    return Scaffold(
      appBar: AppBar(
        automaticallyImplyLeading: !wide,
        title: title == null ? null : Text(title!),
        actions: actions,
      ),
      body: Column(
        children: [
          Expanded(
            child: SingleChildScrollView(
              controller: controller,
              padding: EdgeInsets.fromLTRB(20, wide ? 24 : 4, 20, 40),
              child: PageBody(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (step != null) ...[StepProgress(step!), const SizedBox(height: 28)],
                    Headline(headline, lead: lead),
                    const SizedBox(height: 26),
                    ...children,
                    if (bottom != null) ...[const SizedBox(height: 32), Center(child: bottom)],
                  ],
                ),
              ),
            ),
          ),
          if (primary != null || secondary != null) BottomActions(primary: primary, secondary: secondary),
        ],
      ),
    );
  }
}

/// 화면 아래에 붙는 버튼 줄. 모바일은 주 버튼을 꽉 채우고, PC 는 오른쪽에 둔다.
class BottomActions extends StatelessWidget {
  final Widget? primary;
  final Widget? secondary;

  const BottomActions({super.key, this.primary, this.secondary});

  @override
  Widget build(BuildContext context) {
    final wide = Breakpoints.isWide(context);
    return Container(
      decoration: const BoxDecoration(
        color: AppColors.paper,
        border: Border(top: BorderSide(color: AppColors.line)),
      ),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: SafeArea(
        top: false,
        child: PageBody(
          child: Row(
            children: [
              if (secondary != null) secondary!,
              if (secondary != null) SizedBox(width: wide ? 12 : 8),
              if (wide) const Spacer(),
              if (primary != null)
                wide
                    ? ConstrainedBox(constraints: const BoxConstraints(minWidth: 220), child: primary!)
                    : Expanded(child: primary!),
            ],
          ),
        ),
      ),
    );
  }
}

/// 뒤에서 일이 도는 동안의 화면. 숨 쉬듯 커졌다 작아지는 원.
class ProgressCard extends StatelessWidget {
  final String title;
  final String? note;

  /// 0~100. 있으면 진행 막대와 몇 %인지 보여 준다(서버가 단계마다 알려 준다).
  final num? percent;

  const ProgressCard({super.key, required this.title, this.note, this.percent});

  @override
  Widget build(BuildContext context) {
    return AppCard(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 48),
      child: Column(
        children: [
          const Breathing(),
          const SizedBox(height: 26),
          Text(title, textAlign: TextAlign.center, style: AppText.heading),
          if (percent != null) ...[
            const SizedBox(height: 18),
            PercentBar(percent: percent!),
          ],
          const SizedBox(height: 8),
          Text(
            note ?? '녹음 길이에 따라 몇 분 걸릴 수 있어요.\n이 화면을 닫아도 계속 진행돼요.',
            textAlign: TextAlign.center,
            style: AppText.small,
          ),
        ],
      ),
    );
  }
}

/// 진행 막대와 몇 %. 값이 바뀌면 부드럽게 따라간다(서버는 단계마다 띄엄띄엄 알려 준다).
class PercentBar extends StatelessWidget {
  final num percent;

  const PercentBar({super.key, required this.percent});

  @override
  Widget build(BuildContext context) {
    final target = (percent / 100).clamp(0.0, 1.0).toDouble();
    return ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 320),
      child: TweenAnimationBuilder<double>(
        tween: Tween(end: target),
        duration: const Duration(milliseconds: 700),
        curve: Curves.easeOutCubic,
        builder: (context, value, _) => Column(
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(999),
              child: LinearProgressIndicator(
                value: value,
                minHeight: 8,
                backgroundColor: AppColors.sunk,
                color: AppColors.primary,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              '${(value * 100).round()}%',
              style: AppText.small.copyWith(color: AppColors.primary, fontWeight: FontWeight.w700),
            ),
          ],
        ),
      ),
    );
  }
}

class Breathing extends StatefulWidget {
  final double size;

  const Breathing({super.key, this.size = 56});

  @override
  State<Breathing> createState() => _BreathingState();
}

class _BreathingState extends State<Breathing> with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 1800))
    ..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.size;
    return SizedBox(
      width: s * 1.6,
      height: s * 1.6,
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, _) {
          final t = Curves.easeInOut.transform(_controller.value);
          return Stack(
            alignment: Alignment.center,
            children: [
              Container(
                width: s * (1.15 + 0.45 * t),
                height: s * (1.15 + 0.45 * t),
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: AppColors.primary.withValues(alpha: 0.10 * (1 - t) + 0.04),
                ),
              ),
              Container(
                width: s,
                height: s,
                decoration: const BoxDecoration(shape: BoxShape.circle, color: AppColors.primarySoft),
                alignment: Alignment.center,
                child: SvgPicture.asset(
                  'assets/brand/talkto_mark.svg',
                  height: s * 0.42,
                  colorFilter: const ColorFilter.mode(AppColors.primary, BlendMode.srcIn),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class ErrorBox extends StatelessWidget {
  final String message;

  const ErrorBox(this.message, {super.key});

  @override
  Widget build(BuildContext context) {
    return Notice(message, icon: Icons.error_outline_rounded, color: AppColors.danger, background: AppColors.dangerSoft);
  }
}

/// 화면을 처음 읽지 못했을 때(배포 중 서버 재시작·잠깐 끊긴 연결): 까닭과 '다시 불러오기'.
/// 홈 화면 앱(PWA)에는 브라우저 새로고침 단추가 없어서, 이 단추가 없으면 그대로 멈춘다.
class LoadFailed extends StatelessWidget {
  final Object error;
  final VoidCallback onRetry;

  const LoadFailed(this.error, {super.key, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ErrorBox(error.toString()),
            const SizedBox(height: 12),
            TextButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh_rounded, size: 18),
              label: const Text('다시 불러오기'),
            ),
          ],
        ),
      ),
    );
  }
}

/// 안내 한 줄. 기본은 따뜻한 흙색.
class Notice extends StatelessWidget {
  final String message;
  final IconData icon;
  final Color color;
  final Color background;
  final Widget? trailing;

  const Notice(
    this.message, {
    super.key,
    this.icon = Icons.info_outline_rounded,
    this.color = AppColors.clay,
    this.background = AppColors.claySoft,
    this.trailing,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(color: background, borderRadius: BorderRadius.circular(14)),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(padding: const EdgeInsets.only(top: 1), child: Icon(icon, size: 19, color: color)),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              keepAll(message),
              style: TextStyle(color: Color.lerp(color, AppColors.ink, 0.35), fontSize: 14, height: 1.55),
            ),
          ),
          if (trailing != null) trailing!,
        ],
      ),
    );
  }
}

/// 이름 첫 글자 동그라미. 사람마다 차분한 색 하나를 골라 준다.
class Avatar extends StatelessWidget {
  final String name;
  final double size;

  const Avatar(this.name, {super.key, this.size = 44});

  static const _tones = [
    (Color(0xFFDDE6DF), Color(0xFF2E5A48)),
    (Color(0xFFEEE3D5), Color(0xFF7A5434)),
    (Color(0xFFDFE3EA), Color(0xFF3E4C66)),
    (Color(0xFFEADDDC), Color(0xFF7A4040)),
    (Color(0xFFE6E3D2), Color(0xFF5D5833)),
  ];

  @override
  Widget build(BuildContext context) {
    final trimmed = name.trim();
    final tone = _tones[trimmed.runes.fold(0, (a, b) => a + b) % _tones.length];
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(color: tone.$1, shape: BoxShape.circle),
      child: Text(
        trimmed.isEmpty ? '?' : trimmed.characters.first,
        style: TextStyle(
          fontFamily: AppText.serif,
          fontFamilyFallback: const ['NotoSansKR'],
          color: tone.$2,
          fontWeight: FontWeight.w700,
          fontSize: size * 0.42,
          height: 1.1,
        ),
      ),
    );
  }
}

class FieldLabel extends StatelessWidget {
  final String text;
  final String? hint;

  const FieldLabel(this.text, {super.key, this.hint});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(text, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
          if (hint != null) ...[const SizedBox(height: 3), Text(hint!, style: AppText.small)],
        ],
      ),
    );
  }
}

/// 카드 안 묶음 제목("그분에 대해" 등).
class SectionLabel extends StatelessWidget {
  final String text;

  const SectionLabel(this.text, {super.key});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12, left: 4),
      child: Text(
        text,
        style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: AppColors.muted, letterSpacing: 0.2),
      ),
    );
  }
}

/// 작은 상태 꼬리표.
class StatusChip extends StatelessWidget {
  final String label;
  final bool positive;
  final bool busy;

  const StatusChip(this.label, {super.key, this.positive = false, this.busy = false});

  @override
  Widget build(BuildContext context) {
    final fg = positive ? AppColors.primary : AppColors.ink2;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: positive ? AppColors.primarySoft : AppColors.sunk,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (busy) ...[
            SizedBox(width: 10, height: 10, child: CircularProgressIndicator(strokeWidth: 1.6, color: fg)),
            const SizedBox(width: 6),
          ] else if (positive) ...[
            Container(
              width: 6,
              height: 6,
              decoration: const BoxDecoration(color: AppColors.primary, shape: BoxShape.circle),
            ),
            const SizedBox(width: 6),
          ],
          // 긴 말(예: '첫 100가족 할인가')은 좁은 화면에서 줄을 바꾼다
          Flexible(
            child: Text(
              keepAll(label),
              style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: fg),
            ),
          ),
        ],
      ),
    );
  }
}

/// 화면 가운데의 부드러운 로딩.
class Loading extends StatelessWidget {
  const Loading({super.key});

  @override
  Widget build(BuildContext context) {
    return const Center(
      child: SizedBox(width: 28, height: 28, child: CircularProgressIndicator(strokeWidth: 2.6)),
    );
  }
}

void showMessage(BuildContext context, Object error) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(error.toString())));
}

/// 지운 뒤 안내(서버가 정한 글 그대로 — 서버 백업에 남는 날까지 적혀 있다). 길어서 오래 보인다.
void showDeletionNote(BuildContext context, String text) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(text), duration: const Duration(seconds: 10)));
}

/// 되돌릴 수 없는 일 전에 한 번 묻는다.
Future<bool> confirmAction(
  BuildContext context,
  String title,
  String body, {
  String action = '지우기',
  bool danger = true,
}) async {
  final result = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: Text(body, style: AppText.lead),
      actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('취소')),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          style: FilledButton.styleFrom(
            backgroundColor: danger ? AppColors.danger : AppColors.primary,
            minimumSize: const Size(0, 44),
          ),
          child: Text(action),
        ),
      ],
    ),
  );
  return result ?? false;
}

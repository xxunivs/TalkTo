import 'package:flutter/material.dart';

import '../../core/platform/browser.dart';
import '../../core/theme.dart';

typedef PickedFile = ({String name, List<int> bytes});

/// PC 에서 파일 여러 개를 화면에 끌어다 놓으면 받는다. 끌고 있는 동안 안내를 덮어 보인다.
/// allowed(확장자, 점 없이 소문자)에 없는 파일은 빼고 onRejected 로 알려 준다.
class FileDropArea extends StatefulWidget {
  final Widget child;
  final Set<String> allowed;
  final bool enabled;
  final String hint;
  final void Function(List<PickedFile> files) onFiles;
  final void Function(List<String> names)? onRejected;

  /// 파일 하나 크기 상한(바이트). 넘는 파일은 읽지 않고(큰 영상을 통째로 메모리에 올리지 않게)
  /// onTooBig 으로 이름만 알려 준다. null 이면 제한 없음.
  final int? maxBytes;
  final void Function(List<String> names)? onTooBig;

  const FileDropArea({
    super.key,
    required this.child,
    required this.allowed,
    required this.onFiles,
    this.onRejected,
    this.maxBytes,
    this.onTooBig,
    this.enabled = true,
    this.hint = '여기에 놓으면 올라가요',
  });

  @override
  State<FileDropArea> createState() => _FileDropAreaState();
}

class _FileDropAreaState extends State<FileDropArea> {
  late void Function() _stop;
  bool _hovering = false;

  /// 다른 화면이 위에 덮여 있으면 받지 않는다(여러 화면이 같은 문서에 귀를 대고 있다).
  bool get _onTop => ModalRoute.of(context)?.isCurrent ?? true;

  @override
  void initState() {
    super.initState();
    _stop = listenFileDrop(
      onHover: (h) {
        if (mounted && _onTop && h != _hovering) setState(() => _hovering = h);
      },
      maxBytes: () => mounted && widget.enabled && _onTop ? widget.maxBytes : null,
      onTooBig: (names) {
        if (mounted && widget.enabled && _onTop) widget.onTooBig?.call(names);
      },
      onFiles: (files) {
        if (!mounted || !widget.enabled || !_onTop) return;
        final ok = <PickedFile>[];
        final rejected = <String>[];
        for (final f in files) {
          final dot = f.name.lastIndexOf('.');
          final ext = dot < 0 ? '' : f.name.substring(dot + 1).toLowerCase();
          if (widget.allowed.contains(ext)) {
            ok.add(f);
          } else {
            rejected.add(f.name);
          }
        }
        if (rejected.isNotEmpty) widget.onRejected?.call(rejected);
        if (ok.isNotEmpty) widget.onFiles(ok);
      },
    );
  }

  @override
  void dispose() {
    _stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        widget.child,
        if (_hovering && widget.enabled)
          Positioned.fill(
            child: IgnorePointer(
              child: Container(
                margin: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: AppColors.primarySoft.withValues(alpha: 0.92),
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(color: AppColors.primary, width: 2),
                ),
                alignment: Alignment.center,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.file_download_outlined, size: 40, color: AppColors.primaryDeep),
                    const SizedBox(height: 10),
                    Text(
                      widget.hint,
                      style: const TextStyle(
                        fontSize: 17,
                        fontWeight: FontWeight.w700,
                        color: AppColors.primaryDeep,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
      ],
    );
  }
}

/// 파일 고르기 버튼 아래에 붙이는 안내(여러 개 고르기·끌어다 놓기).
class MultiPickHint extends StatelessWidget {
  final String text;

  const MultiPickHint({
    super.key,
    this.text = '여러 개를 한 번에 고를 수 있어요. 컴퓨터에서는 Ctrl 키(맥은 ⌘)를 누른 채로 파일을 하나씩 누르거나, '
        '파일을 이 화면에 끌어다 놓아도 돼요.',
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Padding(
            padding: EdgeInsets.only(top: 2),
            child: Icon(Icons.info_outline_rounded, size: 15, color: AppColors.muted),
          ),
          const SizedBox(width: 6),
          Expanded(child: Text(text, style: AppText.small)),
        ],
      ),
    );
  }
}

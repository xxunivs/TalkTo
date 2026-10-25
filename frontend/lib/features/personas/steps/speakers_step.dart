import 'dart:async';

import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';

import '../../../core/theme.dart';
import '../../../shared/widgets/ui.dart';
import '../data/persona_api.dart';
import '../persona_flow_page.dart' show PersonaMenu;
import '../../../shared/korean.dart';

/// 3단계: 그분 목소리 찾기.
///
/// 서버가 녹음 여러 개에서 같은 사람의 목소리를 하나로 묶어 둔다. 녹음마다 고르지 않고
/// 목소리 카드를 들어보고 그분 것을 고른다. 한 사람이 둘로 나뉘어 묶였으면 여럿을 고른다.
class SpeakersStep extends StatefulWidget {
  final Map<String, dynamic> detail;
  final VoidCallback onChanged;

  const SpeakersStep({super.key, required this.detail, required this.onChanged});

  @override
  State<SpeakersStep> createState() => _SpeakersStepState();
}

class _SpeakersStepState extends State<SpeakersStep> {
  final _player = AudioPlayer();
  late final Set<String> _chosen = {
    for (final key in (widget.detail['subjectVoices'] as List? ?? const [])) key as String,
  };
  String? _playing;
  bool _busy = false;
  bool _showShort = false;

  List<Map<String, dynamic>> get _voices =>
      [for (final v in widget.detail['voices'] as List? ?? const []) Map<String, dynamic>.from(v)];

  @override
  void initState() {
    super.initState();
    _player.playerStateStream.listen((state) {
      final stopped = !state.playing || state.processingState == ProcessingState.completed;
      if (stopped && mounted && _playing != null) setState(() => _playing = null);
    });
  }

  @override
  void dispose() {
    _player.dispose();
    super.dispose();
  }

  Future<void> _play(Map<String, dynamic> voice) async {
    final key = voice['voiceKey'] as String;
    if (_playing == key) {
      await _player.pause();
      return;
    }
    try {
      await _player.setUrl(voice['previewUrl'] as String);
      setState(() => _playing = key);
      unawaited(_player.play());
    } catch (_) {
      if (mounted) showMessage(context, '재생하지 못했어요.');
    }
  }

  void _toggle(String key) => setState(() => _chosen.contains(key) ? _chosen.remove(key) : _chosen.add(key));

  Future<void> _backToUpload() async {
    setState(() => _busy = true);
    await _player.pause();
    try {
      await PersonaApi.backToUpload(widget.detail['id']);
      widget.onChanged();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _confirm() async {
    setState(() => _busy = true);
    await _player.pause();
    try {
      await PersonaApi.chooseVoices(widget.detail['id'], _chosen.toList());
      widget.onChanged();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  static String _duration(int seconds) => seconds >= 60 ? '${seconds ~/ 60}분 ${seconds % 60}초' : '$seconds초';

  @override
  Widget build(BuildContext context) {
    final voices = _voices;
    // 2초도 안 되는 목소리(맞장구·잡음)는 접어 둔다
    final main = voices.where((v) => (v['speechSeconds'] as int) >= 2 || v['suggested'] == true).toList();
    final short = voices.where((v) => !main.contains(v)).toList();
    final recordings = widget.detail['recordings'] as List;
    final failed = [
      for (final r in recordings)
        if (r['status'] == 'FAILED') r
    ];
    final phone = recordings.where((r) => r['phoneBand'] == true).length;
    final title = personaTitle(widget.detail);

    return WizardScaffold(
      title: title,
      step: 2,
      actions: [PersonaMenu(detail: widget.detail)],
      headline: '어느 목소리가\n$title인가요?',
      lead: '녹음 속 목소리를 사람별로 나눠 두었어요. 들어 보고 $title 목소리를 골라 주세요.',
      secondary: TextButton.icon(
        onPressed: _busy ? null : _backToUpload,
        icon: const Icon(Icons.add_rounded, size: 18),
        label: Text(Breakpoints.isWide(context) ? '녹음 더 올리기' : '녹음 추가'),
      ),
      primary: FilledButton(
        onPressed: _busy || _chosen.isEmpty ? null : _confirm,
        child: Text(_busy
            ? '잠시만요…'
            : _chosen.isEmpty
                ? '목소리를 골라주세요'
                : '이 목소리로 할게요'),
      ),
      children: [
        if (phone > 0) ...[
          Notice(
            '통화 녹음이 $phone개 있어요. 통화 녹음은 음질에 따라 $title 목소리와 닮은 정도가 달라질 수 있어요.',
            icon: Icons.phone_in_talk_outlined,
          ),
          const SizedBox(height: 12),
        ],
        if (failed.isNotEmpty) ...[
          ErrorBox([
            for (final r in failed) '${r['originalName']}: ${r['error'] ?? '이 녹음은 살펴보지 못했어요.'}',
          ].join('\n')),
          const SizedBox(height: 12),
        ],
        for (final voice in main) ...[_voiceCard(voice), const SizedBox(height: 12)],
        if (short.isNotEmpty)
          Center(
            child: TextButton.icon(
              onPressed: () => setState(() => _showShort = !_showShort),
              icon: Icon(_showShort ? Icons.expand_less_rounded : Icons.expand_more_rounded),
              label: Text(_showShort ? '짧게 나온 목소리 접기' : '짧게 나온 목소리 ${short.length}개 더 보기'),
            ),
          ),
        if (_showShort)
          for (final voice in short) ...[const SizedBox(height: 12), _voiceCard(voice)],
        const SizedBox(height: 14),
        const Text('한 분 목소리가 둘로 나뉘어 보이면 둘 다 골라 주세요.', style: AppText.small),
      ],
    );
  }

  Widget _voiceCard(Map<String, dynamic> voice) {
    final key = voice['voiceKey'] as String;
    final selected = _chosen.contains(key);
    final playing = _playing == key;
    final recordings = (voice['recordings'] as List).cast<String>();
    final lines = (voice['lines'] as List).cast<String>();
    final hasPreview = voice['previewUrl'] != null;
    return Material(
      color: selected ? const Color(0xFFF2F6F3) : AppColors.card,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(18),
        side: BorderSide(color: selected ? AppColors.primary : AppColors.line, width: selected ? 1.8 : 1),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: () => _toggle(key),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // 재생 버튼: 카드 고르기와 따로 눌린다
              Material(
                color: playing ? AppColors.primary : AppColors.primarySoft,
                shape: const CircleBorder(),
                child: InkWell(
                  customBorder: const CircleBorder(),
                  onTap: hasPreview ? () => _play(voice) : null,
                  child: SizedBox(
                    width: 48,
                    height: 48,
                    child: Icon(
                      playing ? Icons.pause_rounded : Icons.play_arrow_rounded,
                      size: 26,
                      color: playing ? Colors.white : (hasPreview ? AppColors.primary : AppColors.muted),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(voice['title'], style: AppText.heading),
                        if (voice['suggested'] == true) ...[
                          const SizedBox(width: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                            decoration: BoxDecoration(
                              color: AppColors.claySoft,
                              borderRadius: BorderRadius.circular(999),
                            ),
                            child: const Text(
                              '추천',
                              style: TextStyle(color: AppColors.clay, fontSize: 12, fontWeight: FontWeight.w700),
                            ),
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(
                      '말한 시간 약 ${_duration(voice['speechSeconds'] as int)} · 녹음 ${recordings.length}개'
                      '${hasPreview ? '' : ' · 미리듣기 없음'}',
                      style: AppText.small,
                    ),
                    if (lines.isNotEmpty) ...[
                      const SizedBox(height: 10),
                      for (final line in lines.take(3))
                        Padding(
                          padding: const EdgeInsets.only(top: 4),
                          child: Text(
                            '“$line”',
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(color: AppColors.ink2, fontSize: 14, height: 1.5),
                          ),
                        ),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: 8),
              AnimatedContainer(
                duration: const Duration(milliseconds: 140),
                width: 24,
                height: 24,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: selected ? AppColors.primary : Colors.transparent,
                  border: Border.all(color: selected ? AppColors.primary : AppColors.line2, width: 1.6),
                ),
                child: selected ? const Icon(Icons.check_rounded, size: 16, color: Colors.white) : null,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

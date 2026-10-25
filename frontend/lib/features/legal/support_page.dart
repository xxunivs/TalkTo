import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_svg/flutter_svg.dart';

import '../../core/app_update.dart';
import '../../core/network/api_client.dart';
import '../../core/platform/browser.dart' as browser;
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import 'legal_api.dart';
import 'legal_footer.dart';

/// 고객센터 API (talkto_api_server GET /api/v1/support).
class SupportApi {
  SupportApi._();

  /// {kakaoChannelUrl|null, hours|null, email|null, inquiryCode}. 동의 전에도 열린다.
  static Future<Map<String, dynamic>> load() async => Map<String, dynamic>.from(await ApiClient.get('/support'));
}

/// 고객센터(/support, 대화 화면에서는 /p/:id/support, 동의 화면에서는 /consent/support). 앱 안 문의 게시판은
/// 두지 않는다.
/// 카카오톡 채널(주소가 없으면 단추를 숨긴다)·받는 때·녹음과 사진은 보내지 않는 약속·문의 코드·이메일·
/// 전화(사업자 정보의 대표 전화 — 결제 취소·환불 요청도 받는다, 약관 5번). 맨 아래 개인정보 처리방침 링크.
///
/// 문의 코드는 서버가 준 회원 코드 뒤에 대화 상대 짧은 코드(대화 화면에서 열었을 때)와 앱 버전을 붙인
/// 한 줄로 복사한다: '문의 코드 K7M3-Q9XA · 대화 상대 3F2A9C · 앱 20261021093000'. 관리자는 이 줄을
/// 그대로 붙여 넣어 누구의 어느 대화 상대인지 찾는다(서버 /admin/support/lookup).
class SupportPage extends StatefulWidget {
  /// 대화 화면에서 열었으면 그 대화 상대 id(문의 코드에 짧은 코드를 붙인다).
  final String? personaId;

  /// 동의 화면에서 열었다([beforeConsentPath], 바깥 틀 밖). 넓은 화면에서도 뒤로 가기를 보인다.
  final bool beforeConsent;

  /// 고객센터 내용을 읽는다. 시험에서는 서버 없이 바꿔 끼운다.
  final Future<Map<String, dynamic>> Function() load;

  /// 카카오톡 채널 주소를 연다. 시험에서 바꿔 끼운다.
  final void Function(String url) openUrl;

  /// 복사한다. 시험에서 바꿔 끼운다.
  final Future<void> Function(String text) copy;

  /// 사업자 정보(전화 줄)를 읽는다. 시험에서 바꿔 끼운다.
  final Future<Map<String, dynamic>> Function() loadBusiness;

  const SupportPage({
    super.key,
    this.personaId,
    this.beforeConsent = false,
    this.load = SupportApi.load,
    this.openUrl = openKakaoChannel,
    this.copy = _copy,
    this.loadBusiness = LegalApi.business,
  });

  /// 대화 화면에서 여는 주소(뒤로 가면 그 대화로 돌아온다).
  static String pathFor(String personaId) => '/p/$personaId/support';

  /// 필수 동의 전(동의 화면)에서 여는 주소. 서버 GET /support 는 동의 전에도 열린다. 바뀐 약관을 다시
  /// 받을 때 물어볼 곳이 있도록 동의 게이트가 이 주소는 막지 않는다.
  static const beforeConsentPath = '/consent/support';

  /// 대화 상대 짧은 코드: id 앞 6자(대문자). 서버 support.persona_code 와 같다.
  static String personaCode(String personaId) {
    final plain = personaId.replaceAll('-', '');
    return (plain.length > 6 ? plain.substring(0, 6) : plain).toUpperCase();
  }

  /// 복사할 한 줄. 앱 버전은 배포한 웹 빌드 번호(로컬은 '로컬').
  static String inquiryLine(String code, {String? personaId, String build = appBuild}) => [
        '문의 코드 $code',
        if (personaId != null && personaId.isNotEmpty) '대화 상대 ${personaCode(personaId)}',
        '앱 ${build.isEmpty ? '로컬' : build}',
      ].join(' · ');

  /// 열어도 되는 카카오톡 채널 주소(https://pf.kakao.com/…)만 돌려준다. 서버(support.channel_url)가
  /// 이미 거르지만, 다른 곳·javascript: 주소는 화면에서도 열지 않는다(그러면 단추를 숨긴다).
  static String? channelUrl(Object? value) {
    final text = value?.toString().trim() ?? '';
    final uri = Uri.tryParse(text);
    if (uri == null || uri.scheme != 'https' || uri.host != 'pf.kakao.com') return null;
    if (uri.userInfo.isNotEmpty || uri.hasPort || uri.hasQuery || uri.hasFragment) return null;
    return text;
  }

  /// 카카오톡 채널 열기: 카카오톡 안 브라우저면 그 자리에서(카카오톡이 채널 채팅으로 바로 연다),
  /// 그 밖에는 새 창으로.
  static void openKakaoChannel(String url) {
    if (browser.isKakaoInApp()) {
      browser.goToUrl(url);
    } else {
      browser.openInNewTab(url);
    }
  }

  static Future<void> _copy(String text) => Clipboard.setData(ClipboardData(text: text));

  @override
  State<SupportPage> createState() => _SupportPageState();
}

class _SupportPageState extends State<SupportPage> {
  late Future<Map<String, dynamic>> _data = widget.load();
  late final Future<Map<String, dynamic>> _business = widget.loadBusiness();

  static String? _text(Object? value) {
    final text = value?.toString().trim() ?? '';
    return text.isEmpty ? null : text;
  }

  Future<void> _copy(String text, String done) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.copy(text);
      messenger
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text(done)));
    } catch (_) {
      messenger
        ..hideCurrentSnackBar()
        ..showSnackBar(const SnackBar(content: Text('복사하지 못했어요. 길게 눌러 직접 복사해 주세요.')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final wide = Breakpoints.isWide(context);
    return Scaffold(
      appBar: AppBar(
        automaticallyImplyLeading: !wide || widget.personaId != null || widget.beforeConsent,
        title: const Text('고객센터'),
      ),
      body: FutureBuilder<Map<String, dynamic>>(
        future: _data,
        builder: (context, snap) {
          if (snap.hasError) {
            final error = snap.error;
            return LoadFailed(
              error is ApiException ? error.message : '고객센터 안내를 불러오지 못했어요. 잠시 뒤 다시 해 주세요.',
              onRetry: () => setState(() {
                _data = widget.load();
              }),
            );
          }
          final data = snap.data;
          if (data == null) return const Loading();
          return ListView(
            padding: EdgeInsets.fromLTRB(20, wide ? 24 : 4, 20, 48),
            children: [PageBody(child: _body(data))],
          );
        },
      ),
    );
  }

  Widget _body(Map<String, dynamic> data) {
    final kakao = SupportPage.channelUrl(data['kakaoChannelUrl']);
    final hours = _text(data['hours']);
    final email = _text(data['email']);
    final code = _text(data['inquiryCode']) ?? '';
    final line = SupportPage.inquiryLine(code, personaId: widget.personaId);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Headline(
          '고객센터',
          lead: kakao != null ? '카카오톡 채널로 물어보세요. 읽고 차례로 답해 드려요.' : '이메일로 물어보세요. 읽고 차례로 답해 드려요.',
        ),
        if (hours != null) ...[
          const SizedBox(height: 14),
          Row(
            children: [
              const Icon(Icons.schedule_rounded, size: 19, color: AppColors.primary),
              const SizedBox(width: 8),
              Expanded(child: Text(keepAll('답해 드리는 때: $hours'), style: AppText.lead)),
            ],
          ),
        ],
        const SizedBox(height: 22),
        const SectionLabel('문의 코드'),
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                keepAll('아래 코드를 복사해서 채팅 첫 줄에 붙여 주세요. 누구의 문의인지 바로 찾을 수 있어요.'),
                style: AppText.lead,
              ),
              const SizedBox(height: 14),
              SelectableText(
                code,
                style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800, letterSpacing: 2, color: AppColors.ink),
              ),
              const SizedBox(height: 4),
              SelectableText(line, style: AppText.small),
              const SizedBox(height: 14),
              OutlinedButton.icon(
                onPressed: code.isEmpty ? null : () => _copy(line, '문의 코드를 복사했어요. 채팅 첫 줄에 붙여 주세요.'),
                style: OutlinedButton.styleFrom(minimumSize: const Size(0, 52)),
                icon: const Icon(Icons.copy_rounded, size: 20),
                label: const Text('문의 코드 복사하기'),
              ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        const Notice(
          "녹음·사진 파일은 카카오톡으로 보내지 마세요. 녹음은 앱의 '녹음 올리기'로 올려 주세요.",
          icon: Icons.mic_off_outlined,
        ),
        if (kakao != null) ...[
          const SizedBox(height: 22),
          // 카카오 로그인 단추와 같은 색(카카오톡으로 간다는 것을 한눈에)
          FilledButton.icon(
            onPressed: () => widget.openUrl(kakao),
            style: FilledButton.styleFrom(
              minimumSize: const Size(0, 56),
              backgroundColor: AppColors.kakao,
              foregroundColor: const Color(0xD9000000),
            ),
            icon: SvgPicture.asset('assets/brand/kakao_symbol.svg', width: 20, height: 20),
            label: const Text('카카오톡 채널로 문의하기'),
          ),
        ],
        if (email != null) ...[
          const SizedBox(height: 28),
          const SectionLabel('이메일'),
          AppCard(
            padding: const EdgeInsets.fromLTRB(18, 12, 8, 12),
            child: Row(
              children: [
                const Icon(Icons.mail_outline_rounded, size: 20, color: AppColors.primary),
                const SizedBox(width: 10),
                Expanded(child: SelectableText(email, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600))),
                TextButton(
                  onPressed: () => _copy(email, '이메일 주소를 복사했어요.'),
                  child: const Text('주소 복사'),
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          Text(keepAll('이메일에도 문의 코드를 붙여 주시면 더 빨리 찾아 드려요.'), style: AppText.small),
        ],
        _phone(),
        const SizedBox(height: 36),
        const LegalFooter(compact: true),
      ],
    );
  }

  /// 전화 줄(서버 사업자 정보의 대표 전화). 값이 없거나 못 읽으면 보이지 않는다.
  Widget _phone() {
    return FutureBuilder<Map<String, dynamic>>(
      future: _business,
      builder: (context, snap) {
        final phone = _text(snap.data?['phone']);
        if (phone == null) return const SizedBox.shrink();
        return Padding(
          padding: const EdgeInsets.only(top: 22),
          child: AppCard(
            padding: const EdgeInsets.fromLTRB(18, 12, 8, 12),
            child: Row(
              children: [
                const Icon(Icons.call_outlined, size: 20, color: AppColors.primary),
                const SizedBox(width: 10),
                Expanded(
                  child: SelectableText('전화 $phone', style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                ),
                TextButton(
                  onPressed: () => _copy(phone, '전화번호를 복사했어요.'),
                  child: const Text('번호 복사'),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import 'legal_api.dart';
import 'legal_text.dart';

/// 화면 아래 법정 표시(전자상거래법 제10조 제1항 · 개인정보 보호법 제30조).
///
/// 기본: 개인정보 처리방침(굵게) · 이용약관 · 환불 정책 링크와 사업자 정보를 글로 그대로 보인다
/// (상호·대표·주소·전화·이메일은 초기 화면에 표시해야 해서 '사업자 정보' 단추만으로는 모자라다).
/// 로그인 화면·로그인 뒤 첫 화면·요금제 화면 아래에 둔다.
///
/// [compact]: 한 줄 작은 글 '개인정보 처리방침 · 이용약관 · 사업자 정보'(사업자 정보는 창으로).
/// 정보를 받는 화면(만들기 단계·가입 동의·기억 인터뷰 신청 등) 아래에 둔다.
///
/// 값은 모두 서버(GET /legal 의 business)에서 받는다. 주소·전화·등록번호를 화면 코드에 적지 않는다.
class LegalFooter extends StatelessWidget {
  final bool compact;

  /// 사업자 정보를 읽는다. 시험에서는 서버 없이 바꿔 끼운다.
  final Future<Map<String, dynamic>> Function() loadBusiness;

  const LegalFooter({super.key, this.compact = false, this.loadBusiness = LegalApi.business});

  /// 문서 열기: 공개 주소(/legal/:key, 로그인 없이 열림)로 간다. 라우터 밖(시험·창 안)에서는 창으로 띄운다.
  static void openDoc(BuildContext context, String key) {
    if (GoRouter.maybeOf(context) != null) {
      context.push('/legal/$key');
    } else {
      showLegalDoc(context, key);
    }
  }

  @override
  Widget build(BuildContext context) {
    Widget link(String label, VoidCallback onTap, {bool strong = false}) => InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(6),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 6),
            child: Text(
              label,
              style: AppText.small.copyWith(
                fontSize: 12.5,
                color: strong ? AppColors.ink : AppColors.ink2,
                fontWeight: strong ? FontWeight.w700 : null,
              ),
            ),
          ),
        );
    // 개인정보 처리방침은 다른 안내와 구별되게 굵게, 맨 앞에(표준 개인정보 보호지침 제20조)
    final privacy = link('개인정보 처리방침', () => openDoc(context, 'privacy_policy'), strong: true);
    final terms = link('이용약관', () => openDoc(context, 'terms'));

    if (compact) {
      const dot = Text('·', style: TextStyle(fontSize: 12.5, color: AppColors.muted));
      return Wrap(
        alignment: WrapAlignment.center,
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: 2,
        children: [
          privacy,
          dot,
          terms,
          dot,
          link('사업자 정보', () => showBusinessInfo(context)),
        ],
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // 좁은 휴대폰에서는 줄을 넘긴다. 구분점 대신 간격을 둬 줄 끝에 '·' 가 홀로 남지 않게 한다
        Wrap(
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: 10,
          children: [
            privacy,
            terms,
            link('환불 정책', () => openDoc(context, 'refund')),
          ],
        ),
        const SizedBox(height: 4),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 4),
          child: BusinessInfo(load: loadBusiness),
        ),
      ],
    );
  }
}

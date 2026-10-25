import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../features/admin/admin_dashboard_page.dart';
import '../features/admin/consent_review_page.dart';
import '../features/admin/dev_stats_page.dart';
import '../features/admin/feedback_admin_page.dart';
import '../features/admin/guardian_sms_page.dart';
import '../features/admin/interview_admin_page.dart';
import '../features/admin/payments_admin_page.dart';
import '../features/admin/persona_admin_page.dart';
import '../features/admin/persona_import_page.dart';
import '../features/admin/persona_lists_page.dart';
import '../features/auth/auth_callback_page.dart';
import '../features/auth/consent_page.dart';
import '../features/auth/login_page.dart';
import '../features/billing/plans_page.dart';
import '../features/gaps/gap_questions_page.dart';
import '../features/interview/interview_request_page.dart';
import '../features/legal/account_page.dart';
import '../features/legal/legal_doc_page.dart';
import '../features/legal/support_page.dart';
import '../features/notices/notices_page.dart';
import '../features/personas/bereaved_page.dart';
import '../features/personas/consent_recording_page.dart';
import '../features/personas/guardian_page.dart';
import '../features/personas/home_page.dart';
import '../features/personas/materials_page.dart';
import '../features/personas/persona_create_page.dart';
import '../features/personas/persona_edit_page.dart';
import '../features/personas/persona_flow_page.dart';
import '../features/personas/persona_more_page.dart';
import '../features/personas/recordings_page.dart';
import '../features/share/family_page.dart';
import '../features/share/join_page.dart';
import '../features/share/pending_invite.dart';
import '../features/shell/app_shell.dart';
import 'analytics.dart';
import 'session.dart';
import 'theme.dart';

/// 화면 주소. 웹은 주소 뒤 #/p/… 로 남아서 새로고침해도 보던 화면 그대로다.
///   /login          로그인
///   /auth?ticket=   카카오·구글 로그인에서 돌아온 곳
///   /consent?from=  첫 로그인 필수 동의(바깥 틀 밖, 모든 필수 동의를 가입할 때 받는다).
///                   동의가 빠져 있으면 어디서든 여기로 온다
///   /consent/support  동의 화면에서 여는 고객센터(바깥 틀 밖, 동의 전에도 열린다)
///   /legal/:key     약관·안내 문서 한 장(로그인 없이 볼 수 있다)
///   /join/:token    가족 초대 받기(바깥 틀 밖, 로그인 없이 열린다). 로그인 전에 열면 초대를 적어 두고
///                   ([PendingInvite]) 로그인·가입·필수 동의를 마친 뒤 이 화면으로 돌아온다
///   /account        내 계정·동의 내역·회원 탈퇴
///   /support        고객센터(카카오톡 채널·이메일·문의 코드, 내 계정 메뉴에서)
///   /               첫 화면(목록)
///   /new            새 대화 상대 만들기
///   /p/:id          페르소나(만들기 단계 또는 대화)
///   /p/:id/family   가족과 함께(초대 링크·함께 쓰는 가족, 만든 사람만)
///   /p/:id/edit     정보 고치기. ?section=family 가족 알려 주기(가족 표만, 기억 더 채우기의 가족 카드에서.
///                   저장·취소하면 기억 더 채우기로)
///   /p/:id/voice    예전 주소(없어진 똑 닮은 목소리). 대화 화면으로 보낸다
///   /p/:id/recordings  녹음 더하기(다 만든 페르소나)
///   /p/:id/materials   남긴 글(카카오톡·문자·일기…) 모음 · /add 남긴 글 더하기
///   /p/:id/more     기억 더 채우기(Persona Intake 나머지 질문, 다 만든 뒤)
///   /p/:id/gaps     더 알려 주실 기억(대화하다 아직 모르던 이야기, 만든 분과 함께 쓰는 가족). 대화 중에는 묻지 않는다
///                   (대화 화면을 나올 때 하나만 시트로, 또는 이 화면)
///   /p/:id/interview  기억 인터뷰 받기(대표가 만든 분께 이야기를 듣는다, 만든 사람만·나 자신 빼고)
///   /p/:id/support  고객센터(대화 화면 '더 보기'에서). 문의 코드에 그 대화 상대 짧은 코드를 붙인다
///   /p/:id/consent  그분 동의 녹음(살아 계신 분 대화 상대, 만든 분만 — 앱에서 한 번에 녹음해 TalkTo 가 확인)
///   /p/:id/guardian 법정대리인 확인(만 19세 미만 대화 상대, 만 14세 미만은 동의 두 가지와 확인 문자까지)
///   /p/:id/bereaved 신청 자격과 유족 명단(돌아가신 분 대화 상대, 이미 만든 것은 다음에 들어올 때)
///   /notices        알림(앱 안에서만 — 푸시는 보내지 않는다)
///   /plans          요금제·결제. ?persona= 그 페르소나의 요금제(결제는 페르소나마다)
///                   결제 창에서 돌아오면 ?result= / ?error=
///   /admin          관리자(피드백 대시보드)
///   /admin/stats    개발자 화면 · 사용 통계(관리자만)
///   /admin/import   페르소나 가져오기(예전 자료 묶음 → 고른 계정의 대화 상대, 관리자만)
///   /admin/interviews        기억 인터뷰 신청 목록 · /:id 신청 하나(연락처·상태·메모·들은 내용 넣기)
///   /admin/personas/:id/interview  신청 없이 대화 상대에 들은 내용 넣기(관리자만)
///   /admin/feedback  대화 평가(말풍선 ⋯ 고른 것·고친 말, 대화 한마디 · 확인함 표시, 관리자만)
///   /admin/consents · /:id     그분 동의 녹음 검수 목록 · 한 건(듣기·10항목·판정, 관리자만)
///   /admin/personas/:id        대화 상대 관리(유족 명단·직접 알림 문안·이의 접수·멈춤, 관리자만)
///   /admin/holds · /admin/unable  멈춘 대화 상대 · 처리를 멈춘 예전 대화 상대(관리자만)
///   /admin/payments            결제·환불 기록(관리자만)
///   /admin/guardian-sms        법정대리인 확인 문자 보내기 대기열(TalkTo 가 손으로 보낼 때, 관리자만)
///
/// 화면이 바뀔 때마다 사용 통계(screen_view)를 남긴다. [Analytics.trackLocation]
final router = _trackScreens(GoRouter(
  refreshListenable: session,
  redirect: (context, state) {
    final loc = state.matchedLocation;
    final legal = loc.startsWith('/legal/');
    // 가족 초대는 로그인 전에도 연다(누가 초대했는지 보고 로그인한다)
    final public = loc == '/login' || loc == '/auth' || legal || loc.startsWith('/join/');
    if (!session.loggedIn) return public ? null : '/login';
    if (session.needsConsent) {
      // 필수 동의 전에는 동의 화면과 문서, 고객센터(물어볼 곳)만 볼 수 있다. 끝나면 보려던 화면(from)으로 돌아간다.
      if (loc == '/consent' || loc == SupportPage.beforeConsentPath || legal) return null;
      final from = ConsentPage.safeFrom(state.uri.toString());
      return Uri(path: '/consent', queryParameters: from == '/' ? null : {'from': from}).toString();
    }
    // 동의·로그인을 마쳤으면 적어 둔 가족 초대로 이어 간다(없으면 첫 화면)
    if (loc == '/consent') return ConsentPage.safeFrom(state.uri.queryParameters['from'] ?? PendingInvite.path);
    if (loc == '/login') return PendingInvite.path ?? '/';
    if ((loc == '/admin' || loc.startsWith('/admin/')) && !session.isAdmin) return '/';
    return null;
  },
  routes: [
    GoRoute(
      path: '/login',
      builder: (context, state) => LoginPage(error: state.uri.queryParameters['error']),
    ),
    GoRoute(
      path: '/auth',
      builder: (context, state) => AuthCallbackPage(ticket: state.uri.queryParameters['ticket']),
    ),
    GoRoute(
      path: '/consent',
      builder: (context, state) => ConsentPage(from: state.uri.queryParameters['from']),
    ),
    // 동의 화면의 '고객센터'. 바깥 틀(목록·요금제를 읽는다) 밖이라 동의 전에도 열린다(서버 GET /support 도)
    GoRoute(
      path: SupportPage.beforeConsentPath,
      builder: (context, state) => const SupportPage(beforeConsent: true),
    ),
    GoRoute(
      path: '/join/:token',
      builder: (context, state) {
        final token = state.pathParameters['token']!;
        return JoinPage(key: ValueKey(token), token: token);
      },
    ),
    GoRoute(
      path: '/legal/:key',
      builder: (context, state) {
        final key = state.pathParameters['key']!;
        return LegalDocPage(key: ValueKey(key), docKey: key);
      },
    ),
    GoRoute(path: '/admin', builder: (context, state) => const AdminDashboardPage()),
    GoRoute(path: '/admin/stats', builder: (context, state) => const DevStatsPage()),
    GoRoute(path: '/admin/import', builder: (context, state) => const PersonaImportPage()),
    GoRoute(path: '/admin/feedback', builder: (context, state) => const FeedbackAdminPage()),
    GoRoute(path: '/admin/interviews', builder: (context, state) => const InterviewAdminListPage()),
    GoRoute(
      path: '/admin/interviews/:id',
      builder: (context, state) {
        final id = state.pathParameters['id']!;
        return InterviewAdminDetailPage(key: ValueKey(id), requestId: id);
      },
    ),
    GoRoute(
      path: '/admin/personas/:id/interview',
      builder: (context, state) {
        final id = state.pathParameters['id']!;
        return InterviewPersonaAdminPage(key: ValueKey(id), personaId: id);
      },
    ),
    GoRoute(path: '/admin/consents', builder: (context, state) => const ConsentReviewListPage()),
    GoRoute(
      path: '/admin/consents/:id',
      builder: (context, state) {
        final id = state.pathParameters['id']!;
        return ConsentReviewDetailPage(key: ValueKey(id), id: id);
      },
    ),
    GoRoute(
      path: '/admin/personas/:id',
      builder: (context, state) {
        final id = state.pathParameters['id']!;
        return AdminPersonaPage(key: ValueKey(id), personaId: id);
      },
    ),
    GoRoute(path: '/admin/holds', builder: (context, state) => const HoldsAdminPage()),
    GoRoute(path: '/admin/unable', builder: (context, state) => const UnableListPage()),
    GoRoute(path: '/admin/payments', builder: (context, state) => const PaymentsAdminPage()),
    GoRoute(path: '/admin/guardian-sms', builder: (context, state) => const GuardianSmsPage()),
    ShellRoute(
      builder: (context, state, child) => AppShell(location: state.uri.path, child: child),
      routes: [
        GoRoute(
          path: '/',
          pageBuilder: (context, state) => _page(context, state, const HomePage()),
          routes: [
            GoRoute(
              path: 'plans',
              pageBuilder: (context, state) => _page(
                context,
                state,
                PlansPage(
                  // 결제 창에서 돌아올 때마다 새로 그린다
                  key: ValueKey(state.uri.toString()),
                  personaId: state.uri.queryParameters['persona'],
                  result: state.uri.queryParameters['result'],
                  error: state.uri.queryParameters['error'],
                ),
              ),
            ),
            GoRoute(
              path: 'account',
              pageBuilder: (context, state) => _page(context, state, const AccountPage()),
            ),
            GoRoute(
              path: 'support',
              pageBuilder: (context, state) => _page(context, state, const SupportPage()),
            ),
            GoRoute(
              path: 'notices',
              pageBuilder: (context, state) => _page(context, state, const NoticesPage()),
            ),
            GoRoute(
              path: 'new',
              pageBuilder: (context, state) => _page(context, state, const PersonaCreatePage()),
            ),
            GoRoute(
              path: 'p/:id',
              pageBuilder: (context, state) {
                final id = state.pathParameters['id']!;
                return _page(context, state, PersonaFlowPage(key: ValueKey(id), personaId: id));
              },
              routes: [
                // 예전 링크·즐겨찾기는 대화 화면으로
                GoRoute(
                  path: 'voice',
                  redirect: (context, state) => '/p/${state.pathParameters['id']}',
                ),
                GoRoute(
                  path: 'recordings',
                  pageBuilder: (context, state) =>
                      _page(context, state, RecordingsPage(personaId: state.pathParameters['id']!)),
                ),
                GoRoute(
                  path: 'materials',
                  pageBuilder: (context, state) =>
                      _page(context, state, MaterialsPage(personaId: state.pathParameters['id']!)),
                  routes: [
                    GoRoute(
                      path: 'add',
                      pageBuilder: (context, state) =>
                          _page(context, state, MaterialAddPage(personaId: state.pathParameters['id']!)),
                    ),
                  ],
                ),
                GoRoute(
                  path: 'more',
                  pageBuilder: (context, state) =>
                      _page(context, state, PersonaMorePage(personaId: state.pathParameters['id']!)),
                ),
                GoRoute(
                  path: 'gaps',
                  pageBuilder: (context, state) =>
                      _page(context, state, GapQuestionsPage(personaId: state.pathParameters['id']!)),
                ),
                GoRoute(
                  path: 'interview',
                  pageBuilder: (context, state) =>
                      _page(context, state, InterviewRequestPage(personaId: state.pathParameters['id']!)),
                ),
                GoRoute(
                  path: 'edit',
                  pageBuilder: (context, state) => _page(
                    context,
                    state,
                    PersonaEditPage(
                      // 전체 화면과 가족 알려 주기(?section=)를 오가면 새로 읽는다
                      key: ValueKey(state.uri.toString()),
                      personaId: state.pathParameters['id']!,
                      section: state.uri.queryParameters['section'],
                    ),
                  ),
                ),
                GoRoute(
                  path: 'family',
                  pageBuilder: (context, state) =>
                      _page(context, state, FamilyPage(personaId: state.pathParameters['id']!)),
                ),
                GoRoute(
                  path: 'support',
                  pageBuilder: (context, state) =>
                      _page(context, state, SupportPage(personaId: state.pathParameters['id']!)),
                ),
                GoRoute(
                  path: 'consent',
                  pageBuilder: (context, state) =>
                      _page(context, state, ConsentRecordingPage(personaId: state.pathParameters['id']!)),
                ),
                GoRoute(
                  path: 'guardian',
                  pageBuilder: (context, state) =>
                      _page(context, state, GuardianPage(personaId: state.pathParameters['id']!)),
                ),
                GoRoute(
                  path: 'bereaved',
                  pageBuilder: (context, state) =>
                      _page(context, state, BereavedPage(personaId: state.pathParameters['id']!)),
                ),
              ],
            ),
          ],
        ),
      ],
    ),
  ],
));

/// 라우터가 보여 주는 화면이 바뀔 때마다(리다이렉트가 끝난 뒤의 주소로) 통계를 남긴다.
/// 화면 이동을 막거나 바꾸지 않는 듣기 전용이다.
GoRouter _trackScreens(GoRouter router) {
  router.routerDelegate.addListener(() {
    try {
      final current = router.routerDelegate.currentConfiguration;
      if (current.isNotEmpty) Analytics.trackLocation(current.uri);
    } catch (_) {
      // 통계는 화면 이동을 깨지 않는다
    }
  });
  return router;
}

/// 모바일은 옆으로 밀려 들어오고, PC 는 오른쪽 칸만 살짝 바뀐다.
Page<void> _page(BuildContext context, GoRouterState state, Widget child) {
  if (!Breakpoints.isWide(context)) return MaterialPage(key: state.pageKey, child: child);
  return CustomTransitionPage(
    key: state.pageKey,
    child: child,
    transitionDuration: const Duration(milliseconds: 180),
    reverseTransitionDuration: const Duration(milliseconds: 120),
    transitionsBuilder: (context, animation, _, child) => FadeTransition(opacity: animation, child: child),
  );
}

import '../../core/network/api_client.dart';

/// '더 알려 주실 기억'(talkto_api_server app/api/v1/gaps.py).
///
/// 대화하다 대화 상대가 아직 모르는 이야기가 나오면, 서버가 대답을 보낸 뒤에(대답과 따로) 가족이 답할 수
/// 있는 질문으로 모아 둔다. 화면은 대화 중에는 묻지 않고, 대화 화면을 나올 때 하나만 또는 페르소나 화면의
/// '더 알려 주실 기억'에서 보여 준다. 알림(푸시)은 없다.
///
/// 만든 분(주인)과 함께 쓰는 가족이 쓴다. 가족은 만든 분이 '가족에게 물어보기'한 질문만 보고, 가족의 답과
/// 대답 고침은 만든 분이 확인해야 반영된다. 가족에게 물어보기·확인은 만든 분만(가족이 부르면 404).
class GapApi {
  GapApi._();

  static String _base(String personaId) => '/personas/$personaId/gap-questions';

  static Map<String, dynamic> _map(Object? data) =>
      data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};

  /// GET — 만든 분: 열린 질문 + 확인을 기다리는 가족 답·가족 고침. 가족: 물어봐 달라고 한 질문 + 내 답.
  static Future<GapList> list(String personaId) async => GapList.from(await ApiClient.get(_base(personaId)));

  /// 답하기(글 또는 🎤로 옮긴 글). 만든 분은 바로 기억이 되고(answerState applied), 가족은 만든 분 확인을
  /// 기다린다(pending_owner). 돌려준 값은 질문 한 줄.
  static Future<Map<String, dynamic>> answer(String personaId, String questionId, String text) async =>
      _map(await ApiClient.post('${_base(personaId)}/$questionId/answer', {'text': text}));

  /// '잘 모르겠어요'. 만든 분은 질문을 닫고, 가족은 그 가족에게만 숨긴다.
  static Future<Map<String, dynamic>> dismiss(String personaId, String questionId) async =>
      _map(await ApiClient.post('${_base(personaId)}/$questionId/dismiss'));

  /// '가족에게 물어보기'(만든 분, 함께 쓰는 가족이 있을 때만). [ask] 가 false 면 거둔다.
  static Future<Map<String, dynamic>> askFamily(String personaId, String questionId, {bool ask = true}) async =>
      _map(await ApiClient.post('${_base(personaId)}/$questionId/ask-family', {'ask': ask}));

  /// 가족 답을 기억에 넣는다(만든 분). [seen] 은 목록에서 본 그 줄이다. 본 답 글·답한 때를 함께 보내,
  /// 그 사이에 답이 바뀌었으면(다른 가족이 다시 답했으면) 서버가 409 로 막는다(보지 않은 답이 기억에
  /// 들어가지 않게). 그때 화면은 목록을 다시 읽는다.
  static Future<Map<String, dynamic>> approve(String personaId, Map<String, dynamic> seen) async =>
      _map(await ApiClient.post('${_base(personaId)}/${seen['id']}/approve', seenAnswer(seen)));

  /// 가족 답을 넣지 않는다(만든 분). 질문은 다시 열리고 가족의 답은 지워진다. 보내는 것은 [approve] 와 같다.
  static Future<Map<String, dynamic>> reject(String personaId, Map<String, dynamic> seen) async =>
      _map(await ApiClient.post('${_base(personaId)}/${seen['id']}/reject', seenAnswer(seen)));

  /// 만든 분이 본 가족 답 {answerText, answeredAt}(서버 GapDecision). 받은 그대로 돌려보낸다.
  static Map<String, dynamic> seenAnswer(Map<String, dynamic> row) => {
        'answerText': row['answerText'],
        'answeredAt': row['answeredAt'],
      };

  /// 함께 쓰는 가족이 고쳐 준 대답(말투·내용)을 반영한다(만든 분). [seen] 은 목록에서 본 그 줄이다.
  /// 본 고친 말·고른 것을 함께 보내, 가족이 그 사이에 바꿨으면 서버가 409 로 막는다(보지 않은 글이
  /// 반영되지 않게). 그때 화면은 목록을 다시 읽는다.
  static Future<Map<String, dynamic>> approveCorrection(String personaId, Map<String, dynamic> seen) async =>
      _map(await ApiClient.post('/personas/$personaId/corrections/${seen['id']}/approve', seenCorrection(seen)));

  /// 함께 쓰는 가족이 고쳐 준 대답을 반영하지 않는다(만든 분). 보내는 것은 [approveCorrection] 과 같다.
  static Future<Map<String, dynamic>> rejectCorrection(String personaId, Map<String, dynamic> seen) async =>
      _map(await ApiClient.post('/personas/$personaId/corrections/${seen['id']}/reject', seenCorrection(seen)));

  /// 만든 분이 본 가족 고침 {correction, tags}(서버 CorrectionDecision).
  static Map<String, dynamic> seenCorrection(Map<String, dynamic> row) => {
        'correction': row['correction'],
        'tags': [for (final tag in row['tags'] as List? ?? const []) '$tag'],
      };
}

/// 목록 한 번 읽은 것.
class GapList {
  /// 함께 쓰는 가족이 본 목록인가(role == 'member').
  final bool member;

  /// 열린 질문(최근 것부터). 가족은 물어봐 달라고 한 것만.
  final List<Map<String, dynamic>> questions;

  /// 만든 분: 확인을 기다리는 가족 답. 가족: 내가 답해 확인을 기다리는 것.
  final List<Map<String, dynamic>> pendingAnswers;

  /// 만든 분: 확인을 기다리는 가족의 대답 고침. 가족은 늘 빈 목록.
  final List<Map<String, dynamic>> pendingCorrections;

  /// '가족에게 물어보기'를 보일지(만든 분이고 함께 쓰는 가족이 있을 때).
  final bool canAskFamily;

  const GapList({
    this.member = false,
    this.questions = const [],
    this.pendingAnswers = const [],
    this.pendingCorrections = const [],
    this.canAskFamily = false,
  });

  factory GapList.from(Object? data) {
    final map = data is Map ? data : const {};
    List<Map<String, dynamic>> rows(String key) => [
          for (final row in map[key] as List? ?? const [])
            if (row is Map) Map<String, dynamic>.from(row),
        ];
    return GapList(
      member: map['role'] == 'member',
      questions: rows('questions'),
      pendingAnswers: rows('pendingAnswers'),
      pendingCorrections: rows('pendingCorrections'),
      canAskFamily: map['canAskFamily'] == true,
    );
  }

  /// 만든 분이 확인할 것(가족 답 + 가족 고침).
  int get waiting => member ? 0 : pendingAnswers.length + pendingCorrections.length;
}

/// 대화 상대 상세의 gapCount(만든 분: 열린 질문 수, 가족: 물어봐 달라고 한 열린 질문 수). 없으면 0.
int gapCountOf(Map? persona) => (persona?['gapCount'] as num?)?.toInt() ?? 0;

/// 대화 상대 상세의 gapWaiting(만든 분만: 확인을 기다리는 가족 답 + 가족이 고쳐 보낸 대답). 가족·없으면 0.
/// 열린 질문이 없어도 가족이 보낸 것이 기다리면 만든 분이 알 수 있게 배지에 더한다.
int gapWaitingOf(Map? persona) =>
    persona?['role'] == 'member' ? 0 : (persona?['gapWaiting'] as num?)?.toInt() ?? 0;

/// 만든 분께 보일 '더 알려 주실 기억' 한 줄(가족이 보낸 것이 먼저). [count] 는 gapCount, [waiting] 은 gapWaiting.
/// 둘 다 없으면 null.
String? gapOwnerLine(int count, int waiting) {
  if (waiting > 0) return '가족이 보낸 것 $waiting개 확인해 주세요${count > 0 ? ' · 여쭤볼 것 $count개' : ''}';
  if (count > 0) return '여쭤볼 것 $count개 · 아시는 것만 알려 주세요';
  return null;
}

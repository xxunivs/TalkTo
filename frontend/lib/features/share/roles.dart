/// 대화 상대를 보는 사람이 만든 사람(주인)인지, 초대로 함께하는 가족인지.
///
/// 서버 목록·상세의 role: 'owner' | 'member'. 예전 서버는 role 을 주지 않으므로 없으면 주인으로 본다.
/// 함께하는 가족에게는 서버가 그 가족 쪽에서 본 호칭(addressTerm)을 주므로, 제목·조사·인사말은 그대로 맞는다.
library;

import '../../shared/korean.dart';

/// 초대로 함께하는 가족인가(대화·듣기·평가·내 정보 고치기·그만두기만 한다).
bool isMember(Map? persona) => persona?['role'] == 'member';

/// 이 대화 상대를 만든 가족의 이름(함께하는 가족 화면에서 '지은님'처럼 쓴다). 모르면 '가족'.
String ownerNameOf(Map? persona) {
  final name = (persona?['ownerName'] as String?)?.trim() ?? '';
  return name.isEmpty ? '가족' : name;
}

/// 함께하는 가족이 고친 한 줄(말투·내용)이나 더 알려 주실 기억의 답을 보내기 전에 보이는 안내.
/// 가족이 보낸 것은 만든 분이 확인한 뒤에만 반영되고, 만든 분 화면에 보인다(대화 자체는 보이지 않는다).
const memberSendNotice = '만든 분이 확인한 뒤에 반영돼요. 보내신 내용은 만든 분께 보여요.';

/// 주인 화면: 함께 쓰는 가족 수(들어온 사람만).
int membersOf(Map? persona) => (persona?['members'] as num?)?.toInt() ?? 0;

/// 함께하는 가족 상세의 내 정보(MemberMe). 없으면 빈 값.
Map<String, dynamic> memberMeOf(Map? persona) {
  final me = persona?['me'];
  return me is Map ? Map<String, dynamic>.from(me) : <String, dynamic>{};
}

/// "'엄마'라고 불러요" · "'어머님'이라고 불러요".
String callsText(String term) => "'$term'${hasBatchim(term) ? '이라고' : '라고'} 불러요";

/// 함께하는 가족 한 사람을 한 줄로: "철수 · 아들 · '엄마'라고 불러요".
String memberLine(Map profile) {
  String get(String key) => '${profile[key] ?? ''}'.trim();
  return [
    if (get('myName').isNotEmpty) get('myName'),
    if (get('myRelation').isNotEmpty) get('myRelation'),
    if (get('addressTerm').isNotEmpty) callsText(get('addressTerm')),
  ].join(' · ');
}
